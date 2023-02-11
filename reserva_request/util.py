from aws_lambda_powertools.utilities import parameters

from typing import Any
import json
import gspread
from datetime import datetime, timedelta, timezone

import hashlib
import json
import boto3
from functools import wraps


def ret_json(status_code: int, json_dict: dict) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json;charset=UTF-8"},
        "body": json.dumps(json_dict, ensure_ascii=False)
    }


def error_json(title: str, message: str) -> dict[str, Any]:
    return ret_json(400, {'title': title, 'message': message})


def hybrid_dict_cache(
        local_ttl_argname: str = "local_ttl",
        s3_ttl_argname: str = "s3_ttl",
        default_local_ttl: int = 300,
        default_s3_ttl: int = 3600 * 24,
        __local_only: bool = False):

    s3 = boto3.resource('s3')
    s3bucket = s3.Bucket(
        parameters.get_parameter('reserva_bucket_info'))
    cache: dict = {}

    def make_key(f, *args, **kwargs):
        argstr = "a-" + '-'.join(map(str, args))
        sha512 = hashlib.sha512((json.dumps(
            kwargs, sort_keys=True) + argstr).encode('utf-8')).hexdigest()
        return f"{f.__name__}/{sha512}.json"
    '''
    メタデータ: ['Metadata']
    Last Modified: ['LastModified']
    '''

    def try_s3_cache(key: str) -> dict:
        if __local_only:
            return None

        objs = list(s3bucket.objects.filter(Prefix=key))
        if not (len(objs) == 1 and objs[0].key == key):
            # キャッシュミス
            return None

        obj = s3bucket.Object(key)
        res: dict = obj.get()
        metadata = res['Metadata']
        expired: datetime = datetime.fromisoformat(metadata['expired'])
        print(f'expired={expired}')
        if datetime.now(timezone.utc) > expired:
            # キャッシュ切れ
            return None
        # dict で返す
        body = res['Body'].read().decode('utf-8')
        return json.loads(body)

    def regist_s3_cache(key: str, data: dict, expired: datetime):
        if __local_only:
            return

        obj = s3bucket.Object(key)
        obj.put(Body=json.dumps(data, ensure_ascii=False),
                Metadata={'expired': expired.isoformat()})

    def wrapper(f):

        @wraps(f)
        def inner(*args, **kwargs):
            local_ttl: int = default_local_ttl
            if local_ttl_argname in kwargs:
                local_ttl = int(kwargs[local_ttl_argname])
            s3_ttl: int = default_s3_ttl
            if s3_ttl_argname in kwargs:
                s3_ttl = int(kwargs[s3_ttl_argname])
            key: str = make_key(f, *args, **kwargs)
            data = None
            if key in cache:
                (data, local_expire) = cache[key]
                if datetime.now(timezone.utc) > local_expire:
                    cache.pop(key)
                    data = None
                else:
                    return data

            data = try_s3_cache(key)
            if data is None:
                data = f(*args, **kwargs)
                s3_expire = datetime.now(
                    timezone.utc) + timedelta(seconds=s3_ttl)
                regist_s3_cache(key, data, s3_expire)
            cache[key] = (data,
                          datetime.now(timezone.utc) + timedelta(seconds=local_ttl))
            return data

        return inner

    return wrapper


class GSpreadsheetUtil:
    @ classmethod
    def get_workbook(cls):
        apikey = parameters.get_parameter(
            "ichiba_google_apikey", transform="json")
        with open('/tmp/apikey.json', 'w') as f:
            json.dump(apikey, f, indent=4)
        gc = gspread.service_account(filename="/tmp/apikey.json")
        workbook = gc.open_by_key(parameters.get_parameter(
            "ichiba_google_spreadsheet_key"))
        return workbook

    # 事前登録シートから当該メールアドレスを元に登録情報を取り出す
    @ classmethod
    def get_registered_info_from_spreadsheet(cls, workbook, email: str) -> dict[str, str]:
        sheet = workbook.worksheet("事前登録フォーム回答")
        cell_list = sheet.findall(email)

        if len(cell_list) == 0:
            return None

        target_score = 0
        target_row = None
        target_row_no = -1
        start_datevalue = datetime.strptime(
            "2022/01/01 00:00:00", "%Y/%m/%d %H:%M:%S")
        del_row_list = []
        # 本来1行だけのはずだが事前登録フォームで複数行になることがあるのでクレンジングも同時に実施する
        for cell in cell_list:
            row = sheet.row_values(cell.row)  # 当該行全体を取得
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
