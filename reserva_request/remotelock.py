from typing import Any
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities import parameters
import requests
import re
import time
import json
import boto3
from datetime import timedelta, datetime
import calendar
from collections import deque


logger = Logger()


class ResponseError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message


class RemoteLock:
    def __init__(
        self, registered_info: dict[str, Any] = None, rsv_info: dict[str, Any] = None
    ) -> None:
        self.registered_info: dict[str, Any] = registered_info
        self.rsv_info: dict[str, Any] = rsv_info

    # 対象月のイベントを返す。オートロックの情報は意味を持たないので捨てている。
    def get_events(self, target_year: int, target_month: int) -> list[dict]:
        end_of_read: bool = False
        ret = []
        page: int = 1
        while not end_of_read:
            data, meta = self.api(
                method="GET",
                path="events",
                params={"page": page, "per_page": 50},
                with_metadata=True,
            )
            total_page = meta["total_pages"]
            if page == total_page:
                end_of_read = True
            page += 1

            if self.empty_data_check(data, "get_events", "NO EVENTS"):
                return []
            for item in data:
                if item["type"] in ["unlocked_event", "access_denied"]:
                    st_data = {}
                    print(item["attributes"]["time_zone"])
                    occurred_at = datetime.fromisoformat(
                        item["attributes"]["occurred_at"]
                    )
                    st_data["time"] = occurred_at
                    st_data["event_type"] = item["type"]
                    if item["type"] == "unlocked_event":
                        st_data["status"] = item["attributes"]["status"]
                        st_data["user_type"] = item["attributes"][
                            "associated_resource_type"
                        ]
                        st_data["user_id"] = item["attributes"][
                            "associated_resource_id"
                        ]
                    if (
                        occurred_at.year == target_year
                        and occurred_at.month == target_month
                    ):
                        ret.append(st_data)
                    if occurred_at.year < target_year or (
                        occurred_at.year == target_year
                        and occurred_at.month < target_month
                    ):
                        end_of_read = True

        return ret

    # access user を返す。定期予約が設定してある access user のみが返される。
    def get_users(
        self, start_day: datetime, target_day_range: int = 31, exp_day_range=365
    ) -> list[dict]:
        end_of_read: bool = False
        ret = []
        page: int = 1
        while not end_of_read:
            r, meta = self.api(
                method="GET",
                path="access_persons",
                params={"type": ["access_user"], "page": page, "per_page": 50},
                with_metadata=True,
            )
            total_page = meta["total_pages"]
            if page == total_page:
                end_of_read = True
            page += 1

            if self.empty_data_check(r, "get_users", "NO ACCESS USERS"):
                return []

            for g in r:
                department: str = g["attributes"]["department"]
                if department and department.startswith("[{"):
                    deptjson = json.loads(department)
                    target_slots, exception_slots = self.make_calendar_list(
                        access_info_list=deptjson,
                        start_day=start_day,
                        day_range=target_day_range,
                        exp_day_range=exp_day_range,
                    )
                    ret.append(
                        {
                            "type": "access_user",
                            "id": g["id"],
                            "name": g["attributes"]["name"],
                            "email": g["attributes"]["email"],
                            "timeslots": target_slots,
                            "exception_timeslots": exception_slots,
                        }
                    )
        return ret

    # access guest を返す。access guest は数が多いため、ターゲットとなる年月のものだけを抽出する。
    def get_access_guests(self, target_year: int, target_month: int) -> list[dict]:
        ret = []
        ret.extend(self.__get_access_guests("expired", target_year, target_month))
        ret.extend(self.__get_access_guests("current", target_year, target_month))
        ret.extend(self.__get_access_guests("upcoming", target_year, target_month))
        return ret

    def __get_access_guests(
        self, status: str, target_year: int, target_month: int
    ) -> list[dict]:
        end_of_read: bool = False
        ret = []
        page: int = 1
        while not end_of_read:
            data, meta = self.api(
                method="GET",
                path="access_persons",
                params={
                    "type": ["access_guest"],
                    "page": page,
                    "per_page": 50,
                    "sort": "-starts_at",
                    "attributes[status]": [status],
                },
                with_metadata=True,
            )
            total_page = meta["total_pages"]
            if page == total_page:
                end_of_read = True
            page += 1

            if self.empty_data_check(
                data, "get_expired_access_guests", "NO ACCESS GUESTS"
            ):
                return []
            for item in data:
                st_data = self.make_access_guest_data(item)
                day = st_data["timeslots"][0]["day"]
                slot_year: int = int(day[:4])
                slot_month: int = int(day[5:7])
                if slot_year == target_year and slot_month == target_month:
                    ret.append(st_data)
                if slot_year < target_year or (
                    slot_year == target_year and slot_month < target_month
                ):
                    end_of_read = True

        return ret

    def make_slot(self, dtstr: str, dtstr_iso: str, start_time: str, end_time: str):
        return {
            "day": dtstr,
            "start_time": f"{dtstr} {start_time}",
            "end_time": f"{dtstr} {end_time}",
            "start_time_iso": f"{dtstr_iso}T{start_time}:00.000000",
            "end_time_iso": f"{dtstr_iso}T{end_time}:00.000000",
        }

    def make_access_guest_data(self, item) -> dict:
        ga = item["attributes"]
        starts_at = ga["starts_at"]
        ends_at = ga["ends_at"]
        m_start = re.match(
            r"([0-9]+)-([0-9]+)-([0-9]+)T([0-9]+):([0-9]+):([0-9]+)", starts_at
        )
        (s_year, s_month, s_day, s_hour, s_min, s_sec) = m_start.groups()
        m_end = re.match(
            r"([0-9]+)-([0-9]+)-([0-9]+)T([0-9]+):([0-9]+):([0-9]+)", ends_at
        )
        (e_year, e_month, e_day, e_hour, e_min, e_sec) = m_end.groups()

        s_datetime = datetime(
            int(s_year), int(s_month), int(s_day), int(s_hour), int(s_min), int(s_sec)
        )
        e_datetime = datetime(
            int(e_year), int(e_month), int(e_day), int(e_hour), int(e_min), int(e_sec)
        )

        slot1 = datetime(
            int(s_year), int(s_month), int(s_day), 7, 0, 0
        )  # 早朝枠 5:00-9:00
        slot2 = datetime(
            int(s_year), int(s_month), int(s_day), 10, 0, 0
        )  # 午前枠 9:00-13:00
        slot3 = datetime(
            int(s_year), int(s_month), int(s_day), 15, 0, 0
        )  # 午後枠 13:00-17:00
        slot4 = datetime(
            int(s_year), int(s_month), int(s_day), 19, 0, 0
        )  # 夜枠 17:00-21:00

        slots = []
        dtstr: str = f"{s_year}/{s_month}/{s_day}"
        dtstr_iso: str = f"{s_year}-{s_month}-{s_day}"
        if s_datetime < slot1 and slot1 < e_datetime:
            slots.append(self.make_slot(dtstr, dtstr_iso, "05:00", "09:00"))
        if s_datetime < slot2 and slot2 < e_datetime:
            slots.append(self.make_slot(dtstr, dtstr_iso, "09:00", "13:00"))
        if s_datetime < slot3 and slot3 < e_datetime:
            slots.append(self.make_slot(dtstr, dtstr_iso, "13:00", "17:00"))
        if s_datetime < slot4 and slot4 < e_datetime:
            slots.append(self.make_slot(dtstr, dtstr_iso, "17:00", "21:00"))

        return {
            "type": "access_guest",
            "id": item["id"],
            "name": ga["name"],
            "email": ga["email"],
            "timeslots": slots,
        }

    def register_guest(self) -> str:
        r = self.api(method="GET", path="devices", params={"type": ["lock"]})
        if len(r) != 1:
            logger.error({"service": "remotelock", "response": r})
            raise RuntimeError("device must be only one.")

        lock_id = r[0]["id"]
        name = self.__make_guest_name()
        starts_at, ends_at = self.transform_rsv_time()
        r = self.api(
            path="access_persons",
            params={
                "type": "access_guest",
                "attributes": {
                    "name": name,
                    "email": self.registered_info["email"],
                    "generate_pin": True,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                },
            },
        )
        guest_id = r["id"]
        key_no = r["attributes"]["pin"]
        r = self.api(
            path=f"access_persons/{guest_id}/accesses",
            params={
                "attributes": {"accessible_id": lock_id, "accessible_type": "lock"}
            },
        )
        try:
            r = self.api(
                path=f"access_persons/{guest_id}/email/notify",
                params={"attributes": {"days_before": 1}},
            )
        except ResponseError as e:
            if e.status_code == 422:  # 24時間以内の予約だった
                r = self.api(path=f"access_persons/{guest_id}/email/notify")
            else:
                raise

        logger.info(
            {
                "service": "remotelock",
                "command": "create_access_guest",
                "name": name,
                "email": self.registered_info["email"],
                "starts_at": starts_at,
                "ends_at": ends_at,
                "guest_id": guest_id,
            }
        )
        return key_no

    def delete_old_guests(self) -> None:
        remote_lock_expired_days: int = int(
            parameters.get_parameter("remotelock_expired_days_for_access_guest")
        )
        expired_count: int = 0

        end_of_read: bool = False
        page: int = 1
        while not end_of_read:
            data, meta = self.api(
                method="GET",
                path="access_persons",
                params={
                    "type": ["access_guest"],
                    "page": page,
                    "per_page": 50,
                    "sort": "ends_at",
                    "attributes[status][]": ["deactivated", "expired"],
                },
                with_metadata=True,
            )
            total_page = meta["total_pages"]
            if page == total_page:
                end_of_read = True
            page += 1

            for guest in data:
                status = guest["attributes"]["status"]
                ends_at: datetime = datetime.fromisoformat(
                    guest["attributes"]["ends_at"]
                )
                expired_target: datetime = datetime.now() - timedelta(
                    days=remote_lock_expired_days
                )
                if status == "deactivated" or ends_at < expired_target:
                    expired_count += 1
                    id = guest["id"]
                    name = guest["attributes"]["name"]
                    print(
                        f"FOR DEBUG: to delete [{status}][{id}][{name}][{str(ends_at)}]"
                    )
                    r = self.api(method="DELETE", path=f"/access_persons/{id}")

        logger.info(
            {
                "service": "remotelock",
                "command": "delete_old_guests",
                "deleted_count": str(expired_count),
            }
        )

    def cancel_guest(self) -> bool:
        r = self.api(
            method="GET",
            path="access_persons",
            params={"type": ["access_guest"], "sort": "-created_at", "per_page": 50},
        )

        if self.empty_data_check(
            r, "deactivate_access_guest", "NO ACCESS GUEST (ALREADY CANCELLED?)"
        ):
            return []

        rsv_no = self.rsv_info["visible_rsv_no"]
        guest_id = None
        for g in r:
            if rsv_no in g["attributes"]["name"]:
                guest_id = g["id"]
                guest_name = g["attributes"]["name"]
                r = self.api(method="PUT", path=f"access_persons/{guest_id}/deactivate")
                logger.info(
                    {
                        "service": "remotelock",
                        "command": "deactivate_access_guest",
                        "name": guest_name,
                        "email": self.registered_info["email"],
                        "guest_id": guest_id,
                    }
                )

        if guest_id is None:
            logger.warn(
                {
                    "service": "remotelock",
                    "command": "deactivate_access_guest",
                    "rsv_no": rsv_no,
                    "guest_id": "ALREADY CANCELLED",
                }
            )
            return False

        return True

    def update_access_exceptions(self, user: dict, exception_list: list):
        # name, id
        r = self.api(method="GET", path=f'access_persons/{user["id"]}/accesses')
        # ドアが1つなのでr[0]で良い
        schedule_id = r[0]["attributes"]["access_schedule_id"]
        # /schedules/:id
        r = self.api(method="GET", path=f"schedules/{schedule_id}")
        exception_id = r["attributes"]["access_exception_id"]
        r = self.api(
            method="PUT",
            path=f"access_exceptions/{exception_id}",
            params={"attributes": {"dates": exception_list}},
        )
        logger.info(
            {
                "service": "remotelock",
                "user": user["name"],
                "exception_count": len(exception_list),
            }
        )

    def transform_rsv_time(self):
        rsvtime = self.rsv_info["rsv_time"]
        m = re.match(
            r"([0-9]+)/([0-9]+)/([0-9]+) ([0-9]+):([0-9]+)[^0-9]+([0-9]+):([0-9]+)",
            rsvtime,
        )
        (year, month, day, s_hour, s_min, e_hour, e_min) = m.groups()

        # 開始時間にn分のバッファを持たせるためのロジック
        starts_at_datetime = datetime(
            int(year), int(month), int(day), int(s_hour), int(s_min)
        )
        remotelock_buffer_min: int = int(
            parameters.get_parameter("remotelock_buffer_min")
        )
        td_buffer_min = timedelta(minutes=remotelock_buffer_min)
        starts_at_datetime -= td_buffer_min
        # 日付が変更されることはないので hour と min だけ補正する
        s_hour = str(starts_at_datetime.hour)
        s_min = str(starts_at_datetime.minute)

        starts_at = f"{int(year):02}-{int(month):02}-{int(day):02}T{int(s_hour):02}:{int(s_min):02}:00"
        ends_at = f"{int(year):02}-{int(month):02}-{int(day):02}T{int(e_hour):02}:{int(e_min):02}:00"
        return (starts_at, ends_at)

    def __make_guest_name(self) -> str:
        name = f"{self.registered_info['name']} <{self.rsv_info['visible_rsv_no']}> ({self.registered_info['block']}{self.registered_info['kumi']}"
        if self.registered_info["name"] != self.registered_info["member_name"]:
            name += f" {self.registered_info['member_name']} 様方"
        name += ")"
        return name

    def __error(self, params, r: requests.Response):
        logger.error(
            {
                "service": "remotelock",
                "params": params,
                "status_code": r.status_code,
                "message": r.reason,
            }
        )

    def api(
        self,
        path: str,
        params: dict[str, Any] = {},
        method="POST",
        with_metadata: bool = False,
    ) -> dict[str, Any]:
        token = self.__get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.lockstate+json; version=1",
        }
        r = None
        if method == "POST":
            r = requests.post(
                f"https://api.remotelock.jp/{path}", headers=headers, json=params
            )
            if r.status_code == 409:
                logger.warn(
                    {
                        "service": "remotelock",
                        "cause": "duplication error",
                        "params": params,
                    }
                )
            if r.status_code != 201 and r.status_code != 200:
                self.__error(params, r)
                raise ResponseError(r.status_code, r.reason)
        elif method == "GET":
            r = requests.get(
                f"https://api.remotelock.jp/{path}", headers=headers, params=params
            )
            if r.status_code != 200:
                self.__error(params, r)
                raise ResponseError(r.status_code, r.reason)
        elif method == "PUT":
            r = requests.put(
                f"https://api.remotelock.jp/{path}", headers=headers, json=params
            )
            if r.status_code != 200:
                self.__error(params, r)
                raise ResponseError(r.status_code, r.reason)
        elif method == "DELETE":
            # support Too many request
            sleep_time = 3
            while True:
                r = requests.delete(
                    f"https://api.remotelock.jp/{path}",
                    headers=headers,  # no parameters
                )
                if r.status_code == 429:
                    logger.info(
                        {
                            "service": "remotelock",
                            "command": "delete",
                            "reason": "too many request - http status 429",
                            "sleep time": f"{sleep_time} secs",
                        }
                    )
                    time.sleep(sleep_time)
                    sleep_time *= 2
                    continue
                if r.status_code != 204 and r.status_code != 200:
                    self.__error(params, r)
                    raise ResponseError(r.status_code, r.reason)
                break
        else:
            raise RuntimeError(f"method must be POST or GET or PUT ({method})")
        if len(r.content) > 0:
            if with_metadata:
                return r.json()["data"], r.json()["meta"]
            return r.json()["data"]
        else:
            return None

    def __refresh_token(self, remotelock_token: dict[str, str]) -> dict[str, str]:
        logger.info("refresh token...")
        client_key = parameters.get_parameter("remotelock_clientkey", transform="json")
        client_id = client_key["client_id"]
        client_secret = client_key["client_secret"]
        refresh_token = remotelock_token["refresh_token"]
        epoch_now = int(time.time())
        r = requests.post(
            url="https://connect.remotelock.jp/oauth/token",
            params={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        res = json.loads(r.text)
        access_token = res["access_token"]
        refresh_token = res["refresh_token"]
        expires_at = int(epoch_now + res["expires_in"])
        ssm = boto3.client("ssm")
        ssm.put_parameter(
            Name="remotelock_token",
            Value=json.dumps(
                {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": expires_at,
                }
            ),
            Type="String",
            Overwrite=True,
        )
        return parameters.get_parameter(
            "remotelock_token", force_fetch=True, transform="json"
        )

    def __get_token(self) -> dict[str, str]:
        remotelock_token = parameters.get_parameter(
            "remotelock_token", transform="json"
        )
        expires_at: int = int(remotelock_token["expires_at"])
        epoch_now: int = int(time.time())
        if expires_at <= epoch_now + 120:  # 有効期限が切れているか、今から2分以内に有効期限が切れる
            remotelock_token = self.__refresh_token(remotelock_token)
        return remotelock_token["access_token"]

    def empty_data_check(self, data, command, guest_id):
        if data is None or len(data) == 0:
            logger.warn(
                {"service": "remotelock", "command": command, "guest_id": guest_id}
            )
            return True
        return False

    def get_nth_week(self, day):
        return (day - 1) // 7 + 1

    def get_nth_dow(self, year, month, day):
        return self.get_nth_week(day), calendar.weekday(year, month, day)

    # day_range 日後までの予定を決める
    # RemoteLock のユーザの desc に設定してある JSON を処理をする
    # JSON の形式は以下。day-slot-week の組み合わせ and/or unused-date の組み合わせとなる。
    # [
    #  {"day":"Tue",
    #   "slot":["09:00","13:00","13:00","17:00"],
    #   "week":[1]}, ..(同じ形式を複数OK)
    #  {"unused-date", "2023-06-05"}, .. (同じ形式を複数OK)
    # ]
    def make_calendar_list(
        self,
        access_info_list: list[dict],
        start_day: datetime,
        day_range: int,
        exp_day_range: int = 365,
    ):
        weekday_dict = {
            "Mon": 0,
            "Tue": 1,
            "Wed": 2,
            "Thu": 3,
            "Fri": 4,
            "Sat": 5,
            "Sun": 6,
        }
        target_list: list = []
        exception_list: list = []

        # JSON を読む
        # 先に例外日を追加しておく
        for access_info in access_info_list:
            if "unused-date" in access_info:
                dstr = access_info["unused-date"]
                exception_list.append({"start_date": dstr, "end_date": dstr})

        for i in range(0, day_range):
            t: datetime = start_day + timedelta(days=i)

            # RemoteLock 形式 (ISO)
            dstr = f"{t.year:04}-{t.month:02}-{t.day:02}"

            # 日付の形式は Reserva に合わせて YYYY/MM/DD となる (RemoteLock と異なる)
            dtstr = f"{t.year:04}/{t.month:02}/{t.day:02}"

            # 既に例外日に追加されている場合には当該日は処理しない
            skip_flag = False
            for unused_date_entry in exception_list:
                if dstr == unused_date_entry["start_date"]:
                    skip_flag = True
            if skip_flag:
                continue
            # JSON を読む
            for access_info in access_info_list:
                # 例外日のエントリは事前処理済なので処理しない
                if "unused-date" in access_info or not "day" in access_info:
                    continue
                target_wd = weekday_dict[access_info["day"]]
                no, wd = self.get_nth_dow(t.year, t.month, t.day)
                if target_wd == wd:
                    if no in access_info["week"]:
                        # pop するのでキューを複製して使う
                        slot_queue = deque(access_info["slot"])
                        while len(slot_queue) > 0:  # 同一日複数予約に対応するためのループ
                            start_time = slot_queue.popleft()
                            end_time = slot_queue.popleft()
                            start_dt = f"{dtstr} {start_time}"
                            end_dt = f"{dtstr} {end_time}"
                            dtstr_iso = f"{t.year:04}-{t.month:02}-{t.day:02}"
                            start_iso = f"{dtstr_iso}T{start_time}:00.000000"
                            end_iso = f"{dtstr_iso}T{end_time}:00.000000"
                            # target_list の開始時刻/終了時刻は Reserva に合わせるため30分前倒しなどはしない (access guest は鍵設定時、access user はアクセス時間帯を設定時に調整している)
                            target_list.append(
                                {
                                    "day": dtstr,
                                    "start_time": start_dt,
                                    "end_time": end_dt,
                                    "start_time_iso": start_iso,
                                    "end_time_iso": end_iso,
                                }
                            )
                    else:
                        # アクセス不可日を RemoteLock に設定する
                        # アクセス不可日は一括で設定する
                        # 日付の形式は RemoteLock に合わせて YYYY-MM-DD となる (Reserva と異なる)
                        exception_list.append({"start_date": dstr, "end_date": dstr})
        if len(exception_list) == 0:
            # 空だと同期エラーになる仕様なのでデフォルトで1年先をダミーで追加しておく
            # 日付の形式は RemoteLock に合わせて YYYY-MM-DD となる (Reserva と異なる)
            t: datetime = start_day + timedelta(days=exp_day_range)
            dstr = f"{t.year:04}-{t.month:02}-{t.day:02}"
            exception_list.append({"start_date": dstr, "end_date": dstr})

        return target_list, exception_list
