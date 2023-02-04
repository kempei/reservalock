from remotelock import RemoteLock
from typing import Any
from util import GSpreadsheetUtil, ret_json, error_json, hybrid_dict_cache

from aws_lambda_powertools.utilities import parameters
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
import calendar
from datetime import datetime

# logger についてはここに書いておかないと初期化時の injection でエラーになる。
logger = Logger()


class ReservationReporter:
    def __init__(self, start_date: datetime, target_year: int, target_month: int) -> None:
        self.start_date = start_date
        self.target_year = target_year
        self.target_month = target_month
        self.workbook = GSpreadsheetUtil.get_workbook()
        self.remotelock: RemoteLock = RemoteLock()
        self.registered_users: list = None
        self.community_members: list = None
        self.access_users: list = None
        self.access_guests: list = None
        self.today: datetime = datetime.now()

    # 登録ユーザ(registered_users)、町内会員(community_members)、定期登録ユーザ(access_users)、都度登録ゲスト(access_guests) を収集する
    def collect_data(self):
        self.registered_users, self.community_members = self.__get_registered_users_and_community_members()

        _wday, lastday = calendar.monthrange(
            self.target_year, self.target_month)
        self.access_users = self.remotelock.get_users(self.start_date, lastday)
        self.access_guests = self.remotelock.get_access_guests(
            self.target_year, self.target_month)
        logger.info(
            f'{len(self.registered_users)} registered users, {len(self.community_members)} community members, {len(self.access_users)} access users, {len(self.access_guests)} access guests.')

        # 登録ユーザとアクセスゲストを結合する。
        # registered_user の下['guests']にアクセスゲストがぶら下がる
        for guest in self.access_guests:
            email = guest['email']
            if 'access_guest' == guest['type']:
                user = self.registered_users[email]
                user['guests'].append(guest)

    @hybrid_dict_cache(default_local_ttl=3600, default_s3_ttl=43200)
    def __get_registered_users_and_community_members(self):
        # users を取得する
        # 列は { 0:'timestamp', 1:'email', 2:'user_name', 3:'member_name', 4:'block', 5:'kumi', 6:'objective' }
        cell_users = self.workbook.sheet1.get_all_values()
        cell_users.pop(0)  # 先頭行は不要なので削除する
        members = {}
        users = {}
        for cell_user in cell_users:
            user = {}
            user['email'] = cell_user[1]
            user['reg_timestamp'] = cell_user[0]
            user['user_name'] = cell_user[2]
            user['objective'] = cell_user[6]
            user['guests'] = []
            users[cell_user[1]] = user  # email が主キーとなる

            member_name = cell_user[3]
            member_block = cell_user[4]
            member_kumi = cell_user[5]
            member_id = f'{member_block}{member_kumi} {member_name}'
            if not member_id in members:
                members[member_id] = {'id': member_id, 'block': member_block,
                                      'kumi': member_kumi, 'member_name': member_name, 'users': []}
            user['member_id'] = member_id
            members[member_id]['users'].append(user)

        return users, members

    def report(self) -> None:
        reported_members = []
        for member_key in self.community_members:
            member = self.community_members[member_key]
            member_usage_count = 0
            users = member['users']
            for user in users:
                for guest in user['guests']:
                    member_usage_count += len(guest['slots'])
            member['usage_count'] = member_usage_count
            if member_usage_count > 0:
                reported_members.append(member)

        reported_members.sort(key=lambda x: x['usage_count'], reverse=True)

        weekday_list = ['月', '火', '水', '木', '金', '土', '日']
        for member in reported_members:
            print(f'{member["id"]}: {member["usage_count"]}')
            for user in member['users']:
                user['guests'].sort(key=lambda x: x['date'])
                for guest in user['guests']:
                    for slot in guest['slots']:
                        print(
                            f'  {guest["date"]}({weekday_list[guest["weekday"]]}) {slot}')
    '''
    {
        date: "2022-01-06",
        timeslot: "09:00-13:00",
        name: "名前",
        block: "2ブロック2組",
    },
    '''

    def build_reservation_item(self, actor):
        ret = []
        for slot in actor['timeslots']:
            item = {}
            item['start_time'] = slot['start_time_iso']
            item['date'] = slot['start_time_iso'][:10]
            item['timeslot'] = f'{slot["start_time"][-5:]}-{slot["end_time"][-5:]}'
            if actor['type'] == 'access_user':
                item['name'] = actor['name']
                item['block'] = '定期予約(町内会公認団体)'
            else:
                registered_user = self.registered_users[actor['email']]
                item['name'] = registered_user['user_name']
                cm = self.community_members[registered_user['member_id']]
                item['block'] = f'{cm["block"]} {cm["kumi"]} {cm["member_name"]}'
            ret.append(item)
        return ret

    def make_reservation_list(self):
        ret = []
        for user in self.access_users:
            ret.extend(self.build_reservation_item(user))

        for guest in self.access_guests:
            ret.extend(self.build_reservation_item(guest))

        # 昇順でソート
        ret.sort(key=lambda x: x['start_time'])

        return ret

    '''
      {
        start: new Date(),
        title: "test",
        description: "test description", // day only
        color: "info", // primary or secondary
        icon: "repeat", // or person // day only
      },
    '''

    def build_calendar_item(self, actor, scope: str) -> list:
        ret = []
        for slot in actor['timeslots']:
            item = {}
            day_flag: bool = False
            item['start'] = slot['start_time_iso']
            start_time: datetime = datetime.fromisoformat(
                slot['start_time_iso'])
            end_time: datetime = datetime.fromisoformat(slot['end_time_iso'])
            if actor['type'] == 'access_user':
                item['title'] = actor['name']
            else:
                registered_user = self.registered_users[actor['email']]
                item['title'] = registered_user['user_name']

            if start_time.year == self.today.year and start_time.month == self.today.month and start_time.day == self.today.day:
                item['color'] = 'primary'
                if scope == "day":
                    day_flag = True
                    if actor['type'] == 'access_user':
                        item['icon'] = "repeat"  # access_user は定期予約なので repeat
                        item[
                            'description'] = f'{start_time.hour:02}:00-{end_time.hour:02}:00 定期予約(町内会公認団体)'
                    else:
                        # community_member
                        cm = self.community_members[registered_user['member_id']]
                        item['icon'] = "person"
                        item['description'] = f'{start_time.hour:02}:00-{end_time.hour:02}:00 {cm["block"]} {cm["kumi"]} {cm["member_name"]}'

            if start_time < self.today:
                item['color'] = 'secondary'

            if scope == "month" or day_flag:
                ret.append(item)

        return ret

    def make_calendar_list(self, scope: str):
        ret = []

        for user in self.access_users:
            ret.extend(self.build_calendar_item(user, scope))

        for guest in self.access_guests:
            ret.extend(self.build_calendar_item(guest, scope))

        return ret


@logger.inject_lambda_context(log_event=True)
def handler(event: dict, context: LambdaContext) -> dict[str, Any]:
    if not 'queryStringParameters' in event:
        return error_json("Bad parameter", "No parameters")

    params = event['queryStringParameters']

    if not 'format' in params:
        return error_json("Bad parameter", "missing 'format'")
    format: str = params['format']  # reservation or calendar

    scope: str = None
    if 'scope' in params:
        scope = params['scope']  # month or day

    if not 'start' in params:
        return error_json("Bad parameter", "missing 'start'")
    start_date: str = params['start']  # YYYY-MM-DD
    target_year: int = int(start_date[:4])
    target_month: int = int(start_date[5:7])
    target_day: int = int(start_date[8:10])
    if target_year < 2022 or target_year > 3000:
        return error_json('invalid parameter', f'invalid target year {target_year}')
    if target_month < 1 or target_month > 12:
        return error_json('invalid parameter', f'invalid target month {target_month}')

    logger.info(
        f"format: {format}, scope: {scope}, start_date: {start_date}, target_year: {target_year}, target_month: {target_month}, target_day: {target_day}")

    reporter: ReservationReporter = ReservationReporter(
        datetime.fromisoformat(start_date), target_year, target_month)

    reporter.collect_data()

    if format == "reservation":
        return ret_json(200, reporter.make_reservation_list())
    if format == "calendar":
        if scope == "month" or scope == "day":
            return ret_json(200, reporter.make_calendar_list(scope))

    return ret_json(400, {"message": f"Bad parameter: format={format}, scope={scope}"})
