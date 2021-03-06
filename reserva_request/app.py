from multiprocessing import AuthenticationError
from time import time
from remotelock import RemoteLock
from typing import Any
import requests
from bs4 import BeautifulSoup
import gspread
from aws_lambda_powertools.utilities import parameters
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from datetime import datetime, date, timedelta
import json
import base64
import urllib.parse
import calendar
import re
import hashlib
from collections import deque

# logger についてはここに書いておかないと初期化時の injection でエラーになる。
logger = Logger()
session = None

def get_workbook():
    apikey = parameters.get_parameter("ichiba_google_apikey", transform="json")
    with open('/tmp/apikey.json', 'w') as f:
        json.dump(apikey, f, indent=4)
    gc = gspread.service_account(filename="/tmp/apikey.json")
    workbook = gc.open_by_key(parameters.get_parameter("ichiba_google_spreadsheet_key"))
    return workbook
workbook = get_workbook()

# Reserva のアカウントID, Reserva の施設ID (ホール), 何日先まで予約するか
def get_reserva_parameters():
    reserva_system_info = parameters.get_parameter('reserva_systeminfo', transform="json")
    return reserva_system_info['bus_id'], reserva_system_info['svd_id'], reserva_system_info['day_range'], reserva_system_info['auth_token']
RESERVA_BUS_ID, RESERVA_SVD_ID, RESERVA_DAY_RANGE, AUTH_TOKEN = get_reserva_parameters()

def ret_json(status_code:int, json_dict:dict) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": { "Content-Type": "application/json;charset=UTF-8" },
        "body": json.dumps(json_dict, ensure_ascii=False)
    }

def error_json(title:str, message:str) -> dict[str, Any]:
    return ret_json(400, {'title': title, 'message': message})

def reserva_login():
    global session
    session = requests.session()
    reserva_userinfo = parameters.get_parameter('reserva_userinfo', transform="json")
    reserva_userid = reserva_userinfo['userid']
    reserva_pass = reserva_userinfo['password']
    r = session.get("https://reserva.be/rsv/dashboard")
    r = session.post("https://id-sso.reserva.be/login/business",
        data={'next_check_flg': 0,
                'adm_no': '',
                'mode':'login',
                'adm_id': reserva_userid,
                'adm_pass': reserva_pass})
    logger.info("reserva login succeeded")

# Reserva の予約申請メールに含まれる URL を基に、予約関連の情報を取得する
def get_reservation_info_from_reserva(reserva_rsv_url:str) -> dict[str, str]:
    reserva_login()

    r = session.get(reserva_rsv_url)
    soup = BeautifulSoup(r.content, "html.parser")
    left = soup.find(id='div_reserva_left')
    left_dd = left.find_all('dd')
    right = soup.find(id='div_reserva_right')
    right_dd = right.find_all('dd')

    ret = {}
    ret['hidden_rsv_no'] = soup.find("input", attrs={'name': 'search_rsv_no', 'type': 'hidden'})['value']
    ret['rsv_time'] = soup.find("input", attrs={'id': 'zoom_rsv_all_time', 'type': 'hidden'})['value']
    ret['name'] = str(left_dd[0].text).strip()
    ret['name_kana'] = str(left_dd[1].text).strip()
    ret['email'] = str(left_dd[2].text).strip()
    ret['phone'] = str(left_dd[3].text).strip()
    ret['visible_rsv_no'] = str(right_dd[0].text).strip()
    ret['rsv_status'] = str(soup.find(id='span_status').text).strip() # 予約確定 とか
    return ret

# 事前登録シートから当該メールアドレスを元に登録情報を取り出す
def get_registered_info_from_spreadsheet(email:str) -> dict[str, str]:
    sheet = workbook.worksheet("事前登録フォーム回答")
    cell_list = sheet.findall(email)

    if len(cell_list) == 0:
        return None

    target_score = 0
    target_row = None
    target_row_no = -1
    start_datevalue = datetime.strptime("2022/01/01 00:00:00", "%Y/%m/%d %H:%M:%S")
    del_row_list = []
    # 本来1行だけのはずだが事前登録フォームで複数行になることがあるのでクレンジングも同時に実施する
    for cell in cell_list:
        row = sheet.row_values(cell.row) # 当該行全体を取得
        # 2022年1月1日からの秒数をスコアとする
        datevalue = datetime.strptime(row[0], "%Y/%m/%d %H:%M:%S")
        td = datevalue - start_datevalue
        score = td.total_seconds()
        if len(row) == 7 and len(row[6]) > 0:
            # 目的が空じゃない場合は100年分のスコアを加算
            score += 3600 * 24 * 365 * 100
        if target_score < score:
            if target_row_no > 0:
                # 重複している行は削除対象とする
                del_row_list.append(target_row_no)
            target_score = score
            target_row = row
            target_row_no = cell.row
        else:
            # 重複している行は削除対象とする
            del_row_list.append(cell.row)

    # 重複行削除の実施
    if len(del_row_list) > 0:
        del_row_list.sort(reverse=True)
        for row_no in del_row_list:
            sheet.delete_rows(row_no)
    ret = {}
    ret['timestamp'] = target_row[0]
    ret['email'] = target_row[1]
    ret['name'] = target_row[2]
    ret['member_name'] = target_row[3]
    ret['block'] = target_row[4]
    ret['kumi'] = target_row[5]
    if len(target_row) == 7 and len(target_row[6]) > 0:
        ret['objective'] = target_row[6]
    return ret

# Reserva の Ajax API を呼び出す
def reserva_api(rsv_no:str, rsv_status:int, message:str) -> str:
    r = session.post("https://reserva.be/AjaxSearch",
        params={
            'cmd': 'change_rsv_status',
            'rsv_no': rsv_no,
            'rsv_status': rsv_status, # 1: 確定 3: キャンセル
            'text_context': message,
            'is_admin': 1,
            'bus_cd': RESERVA_BUS_ID,
            'mail_context': '', 
            'payment_flg': 0,
            'request_view_type': 'reserve_detail'
        })
    if r.status_code != 200:
        return f"fail (status_code={r.status_code})"
    rd = json.loads(r.text)
    logger.debug({'reserva_api_response': rd})
    err_no:int = int(rd['ret'])
    if err_no > 0:
        if err_no == 1007:
            return "already cancelled"
        return f"fail: [{err_no}] {rd['msg']}"
    return "success"

# 予約承認
def approve(rsv_no:str, key_no:str) -> str:
    return reserva_api(
        rsv_no,
        1,
        f'ご予約ありがとうございます。事前登録に基づき、以下の内容でご予約が確定しました。予約時間帯のみ使用可能な鍵番号は {key_no} です。'
    )

# 予約拒否
def deny(rsv_no:str) -> str:
    return reserva_api(
        rsv_no,
        3,
        '予約には事前登録が必要です。事前登録フォームからメールアドレスをご登録ください。事前登録に関する情報は回覧板にてお伝えしておりますのでご確認ください。'
    )

def append_log_to_spreadsheet(rsv_info, registered_info, log_info):
    sheet = workbook.worksheet("予約承認履歴")
    row = []
    row.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    if registered_info is None:
        row.append(rsv_info['email'])
        row.append(rsv_info['name'])
        row.append("(未登録)")
        row.append("(未登録)")
        row.append("(未登録)")
    else:
        row.append(registered_info['email'])
        row.append(registered_info['name'])
        row.append(registered_info['member_name'])
        row.append(registered_info['block'])
        row.append(registered_info['kumi'])
    row.append(rsv_info['visible_rsv_no'])
    row.append(rsv_info['rsv_time'])
    for l in log_info:
        row.append(l)
    logger.info({'row': row})
    sheet.append_row(row)

# 引数として Reserva の予約申請メールに記載されている確認用の URL が必要
@logger.inject_lambda_context(log_event=True)
def handler(event: dict, context: LambdaContext) -> dict[str, Any]:
    print(event['headers'])
    if not 'authorization' in event['headers']:
        return ret_json(401, "Unauthorized")
    authtoken = event['headers']['authorization']
    if authtoken != AUTH_TOKEN:
        return ret_json(401, "Unauthorized")

    if not 'body' in event:
        return error_json('invalid request', 'body not found')
    body = event['body']
    params = body # test event
    if not isinstance(body, dict): # API Gateway
        if isinstance(body, str) and body.startswith('{'):
            # JSON string - FunctionURL
            params = json.loads(body)
        else:
            # Base64 - API Gateway
            params = urllib.parse.parse_qs(base64.b64decode(body).decode())

    logger.info({'parameter': params})

    if not 'command' in params:
        return error_json('parameter not found', 'command')
    if not 'url' in params:
        return error_json('parameter not found', 'url')

    reserva_url = params['url'][0]
    reserva_command = params['command'][0]
    if not reserva_command in ('request', 'cancel'):
        return error_json('invalid reserva command', f'{reserva_command}')

    rsv_info = get_reservation_info_from_reserva(reserva_url)
    registered_info = get_registered_info_from_spreadsheet(rsv_info['email'])
    log_info = []
    response_code = 200
    remotelock:RemoteLock = RemoteLock(registered_info, rsv_info)
    try:
        if reserva_command == 'request':
            # 予約申請メールが来た場合
            # 既に予約確定済
            if rsv_info['rsv_status'] == '予約確定':
                log_info.append("request: approve")
                log_info.append('既に確定済みです')
            elif rsv_info['rsv_status'] == 'キャンセル':
                log_info.append("request: approve")
                log_info.append('既にキャンセル済みです')
            elif registered_info:
                # 申請OK
                # 鍵番号を発行してから Approve する
                log_info.append("request: approve")
                key_no = remotelock.register_guest()
                approve_status = approve(rsv_info['hidden_rsv_no'], key_no)
                log_info.append(approve_status)
                if approve_status != 'success':
                    remotelock.cancel_guest()
                    if approve_status != 'already cancelled':
                        response_code = 400
            else:
                # 却下
                # 鍵番号はまだ発行されておらず Reserva で却下するだけ (RemoteLock は何もしなくてOK)
                log_info.append("request: deny")
                deny_status = deny(rsv_info['hidden_rsv_no'])
                log_info.append(deny_status)
                if deny_status != 'success':
                    response_code = 400
        elif reserva_command == 'cancel':
            # キャンセル通知が来た場合
            log_info.append("cancel")
            if rsv_info['rsv_status'] != 'キャンセル':
                log_info.append('正しくキャンセルされていません')
            # Reserva は既にキャンセルされているので何もしなくて良くて、RemoteLock のキャンセルのみを行う
            if remotelock.cancel_guest():
                log_info.append("success")
            else:
                log_info.append("RemoteLock側で既にキャンセルされています。")
    except:
        logger.exception("system error")
        log_info.append("system error")
        append_log_to_spreadsheet(rsv_info, registered_info, log_info)
        raise

    append_log_to_spreadsheet(rsv_info, registered_info, log_info)

    return ret_json(response_code, {"log": log_info})


def get_nth_week(day):
    return (day - 1) // 7 + 1

def get_nth_dow(year, month, day):
    return get_nth_week(day), calendar.weekday(year, month, day)

# 3ヶ月先までの予定を決める
def make_calendar_list(access_info_list:list[dict]):
    weekday_dict = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}
    today:datetime = datetime.now()
    target_list:list = []
    exception_list:list = []
    for i in range(RESERVA_DAY_RANGE):
        t:datetime = today + timedelta(days=i)
        for access_info in access_info_list:
            target_wd = weekday_dict[access_info['day']]
            no, wd = get_nth_dow(t.year, t.month, t.day)
            if target_wd == wd:
                if no in access_info['week']:
                    dtstr = f'{t.year:04}/{t.month:02}/{t.day:02}'
                    slot_queue = deque(access_info["slot"]) # pop するのでキューを複製して使う
                    while len(slot_queue) > 0: # 同一日複数予約に対応するためのループ
                        start_dt = f'{dtstr} {slot_queue.popleft()}'
                        end_dt = f'{dtstr} {slot_queue.popleft()}'
                        target_list.append({"day": dtstr, "start_time": start_dt, "end_time": end_dt})
                else:
                    # アクセス不可日を RemoteLock に設定する
                    # アクセス不可日は一括で設定する
                    dstr = f'{t.year:04}-{t.month:02}-{t.day:02}'
                    exception_list.append({"start_date": dstr, "end_date": dstr})
    if len(exception_list) == 0:
        # 空だと同期エラーになる仕様なので1年先をダミーで追加しておく
        t:datetime = today + timedelta(days=365)
        dstr = f'{t.year:04}-{t.month:02}-{t.day:02}'
        exception_list.append({"start_date": dstr, "end_date": dstr})

    return target_list, exception_list


def reserva_check_reservation(schedule: dict):
    params={
        'cmd': 'reserva_admin_check',
        'checkflg': 1,
        'rsv_no': '',
        'rsv_svd_no': RESERVA_SVD_ID,
        'rsv_stf_cd': 'undefined',
        'all_day_flag': 0,
        'reserve_time': f'{schedule["start_time"]}:0@{schedule["end_time"]}:0@',
        'bus_cd': RESERVA_BUS_ID,
        'rsd_room_no': '',
        'rsd_sec_no': 'undefined',
        'select_timeorday': 0,
        'visit_flag': 0,
    }
    r = session.post("https://reserva.be/AjaxSearch",
        params=params
    )
    if r.status_code != 200:
        logger.error(r)
        raise RuntimeError(f"fail (status_code={r.status_code})")
    ret = r.text.split('|')
    del(params['cmd'])
    del(params['checkflg'])
    if int(ret[0]) == 0:
        return params
    else:
        return None

def reserva_make_reservation(user:dict, schedule:dict, check_param:dict):
    # 時間区分を表す svd_sub_no という数字を抽出する
    r = session.get(f'https://reserva.be/rsv/reservations?mode=list_add&callback_url=https://reserva.be/rsv/reservations/calendar')
    r = session.post("https://reserva.be/AjaxSearch",
        params={
            'cmd': 'get_institution_reserve_time',
            'srv_no': RESERVA_SVD_ID,
            'bus_cd': RESERVA_BUS_ID,
            'is_admin': 1,
            'bus_cur_type': 0,
            'rsv_svd_no': RESERVA_SVD_ID,
            'rsv_data': schedule['day'],
            'reserve_date': schedule['day'],
            'rsv_stf_cd': 0,
            'bus_interval': 60
        }
    )
    if r.status_code != 200:
        logger.error(r)
        raise RuntimeError(f"fail (status_code={r.status_code})")
    rjson = json.loads(r.text)
    soup = BeautifulSoup(rjson['fix'], "html.parser")
    time_input_list = soup.find_all('input', attrs={'name': 'rsv_svd_start_time[]', 'type': 'hidden'})
    rsv_svd_subno = -1
    for item in time_input_list:
        if f'{schedule["start_time"]}:00' == item['value']:
            rsv_svd_subno = int(re.sub(r"\D", "", item['id'])) # 数字だけを残す
    if rsv_svd_subno < 0:
        logger.error({
            'service':'reserva',
            'command': 'create_reservation',
            'list': time_input_list
        })
        raise RuntimeError("cannot find rsv_svd_subno in input list")
    
    # 電話番号を email から一意に作成する
    m = hashlib.shake_256()
    m.update(user['email'].encode())
    hex = int(m.hexdigest(2), 16)
    tel = f'04670{hex:06}'

    # 実際の予約を行う
    r = session.post("https://reserva.be/rsv/reservations",
        params={
            'mode': 'list_add',
            'callback_url': 'https://reserva.be/rsv/reservations/calendar'
        },
        data={
            'mode': 'edit_add',
            'g_business_cd': RESERVA_BUS_ID,
            'g_cti_mode': 0,
            'callback_url': 'https://reserva.be/rsv/reservations/calendar',
            'check_rsv_input': 1,
            'bus_reserve_flag': 1,
            'ist_type': 0,
            'admit_flg': 1,
            'rsv_status': 1,
            'rsv_visit_flag': 0,
            'rsv_all_day_flag': 0,
            'cti_memo_update_flg': 0,
            'cti_reservaok_flg': 0,
            'ist_reserve_days_num': 1,
            'reserva_admin_check': json.dumps(check_param),
            'ajaxPage': 1,
            'is_akerun_contract': 0,
            'kok_sai': user['name'],
            'kok_mei': '(市場町内会)',
            'kok_sai_kana': 'イチバチョウナイカイ',
            'kok_mei_kana': 'イチバチョウナイカイ',
            'kok_mail': user['email'],
            'kok_tel': tel,
            'rsv_svd_no': RESERVA_SVD_ID,
            'rsd_group_people': 1,
            'select_timeorday': 1,
            'rsv_data': schedule['day'],
            'rsv_svd_start_time[]': f"{schedule['day']} 06:00:00",
            'rsv_svd_end_time[]': f"{schedule['day']} 09:00:00",
            'rsv_svd_start_time[]': f"{schedule['day']} 09:00:00",
            'rsv_svd_end_time[]': f"{schedule['day']} 13:00:00",
            'rsv_svd_start_time[]': f"{schedule['day']} 13:00:00",
            'rsv_svd_end_time[]': f"{schedule['day']} 17:00:00",
            'rsv_svd_start_time[]': f"{schedule['day']} 17:00:00",
            'rsv_svd_end_time[]': f"{schedule['day']} 21:00:00",
            'rsv_svd_no|rsv_svd_subno[]': f"{RESERVA_SVD_ID}|{rsv_svd_subno}|{schedule['start_time']}:00|{schedule['end_time']}:00|0",
            'rsv_payment': 0,
            'rsv_text': 'システムによる予約',
            'rsv_memo': ''
        }
    )
    if r.status_code != 200:
        logger.error(r)
        raise RuntimeError(f"fail (status_code={r.status_code})")
    logger.info({
        'service':'reserva',
        'command': 'create_reservation',
        'name': user['name'],
        'schedule': schedule
    })

def reserva_create_reservation(user:dict, target_list:list):
    for target in target_list:
        check_param = reserva_check_reservation(target)
        if check_param:
            reserva_make_reservation(user, target, check_param)

@logger.inject_lambda_context(log_event=True)
def batch_handler(event: dict, context: LambdaContext) -> dict[str, Any]:
    remotelock:RemoteLock = RemoteLock()
    users:list[dict] = remotelock.get_users()
    reserva_login()
    for user in users:
        logger.info(user)
        target_list, exception_list = make_calendar_list(user['schedule'])
        if len(target_list) > 0:
            reserva_create_reservation(user, target_list)
        if len(exception_list) > 0:
            remotelock.update_access_exceptions(user, exception_list)
    return ret_json(200, {"message": "finished normally"})
