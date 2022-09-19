from typing import Any
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities import parameters
import requests
import re
import time
import json
import boto3
import datetime

logger = Logger()

class ResponseError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message    

class RemoteLock:
    def __init__(self, registered_info:dict[str, Any] = None, rsv_info:dict[str, Any] = None) -> None:
        self.registered_info:dict[str, Any] = registered_info
        self.rsv_info:dict[str, Any] = rsv_info

    def get_users(self) -> list[dict]:
        r = self.__api(
            method='GET',
            path='access_persons',
            params={
                'type': ['access_user'],
                'per_page': 50
            }
        )
        ret = []
        if r is None or len(r) == 0:
            logger.warn({
                'service':'remotelock',
                'command': 'get_users',
                'guest_id': 'NO ACCESS USERS'
            })
            return ret
        for g in r:
            department:str = g['attributes']['department']
            if department and department.startswith('[{'):
                deptjson = json.loads(department)
                ret.append({
                    'id': g['id'],
                    'name': g['attributes']['name'],
                    'email': g['attributes']['email'],
                    'schedule': deptjson
                })
        return ret

    def register_guest(self) -> str:
        r = self.__api(
            method='GET',
            path='devices',
            params={
                'type': ['lock']
            }
        )
        if len(r) != 1:
            logger.error({'service': 'remotelock', 'response': r})
            raise RuntimeError("device must be only one.")

        lock_id = r[0]['id']
        name = self.__make_guest_name()
        starts_at, ends_at = self.transform_rsv_time()
        r = self.__api(
            path='access_persons',
            params={
                'type': 'access_guest',
                'attributes': {
                    'name': name,
                    'email': self.registered_info['email'],
                    'generate_pin': True,
                    'starts_at': starts_at,
                    'ends_at': ends_at
                }
            }
        )
        guest_id = r['id']
        key_no = r['attributes']['pin']
        r = self.__api(
            path=f'access_persons/{guest_id}/accesses',
            params={
                'attributes': {
                    'accessible_id': lock_id,
                    'accessible_type': 'lock'
                }
            }
        )
        try:
            r = self.__api(
                path=f'access_persons/{guest_id}/email/notify',
                params={
                    'attributes': {
                        'days_before': 1
                    }
                }
            )
        except ResponseError as e:
            if e.status_code == 422: # 24時間以内の予約だった
                r = self.__api(
                    path=f'access_persons/{guest_id}/email/notify'
                )
            else:
                raise

        logger.info({
            'service':'remotelock',
            'command': 'create_access_guest',
            'name': name,
            'email': self.registered_info['email'],
            'starts_at': starts_at,
            'ends_at': ends_at,
            'guest_id': guest_id
        })
        return key_no

    def cancel_guest(self) -> bool:
        r = self.__api(
            method='GET',
            path='access_persons',
            params={
                'type': ['access_guest'],
                'sort': ["-created_at"],
                'per_page': 50
            }
        )
        if r is None or len(r) == 0:
            logger.warn({
                'service':'remotelock',
                'command': 'deactivate_access_guest',
                'guest_id': 'NO ACCESS GUEST (ALREADY CANCELLED?)'
            })
            return False
        rsv_no = self.rsv_info['visible_rsv_no']
        guest_id = None
        for g in r:
            if rsv_no in g['attributes']['name']:
                guest_id = g['id']
                guest_name = g['attributes']['name']
                break
        if guest_id is None:
            logger.warn({
                'service':'remotelock',
                'command': 'deactivate_access_guest',
                'rsv_no': rsv_no,
                'guest_id': 'ALREADY CANCELLED'
            })
            return False

        r = self.__api(
            method='PUT',
            path=f'access_persons/{guest_id}/deactivate'
        )
        logger.info({
            'service':'remotelock',
            'command': 'deactivate_access_guest',
            'name': guest_name,
            'email': self.registered_info['email'],
            'guest_id': guest_id
        })
        return True

    def update_access_exceptions(self, user:dict, exception_list:list):
        # name, id
        r = self.__api(
            method='GET',
            path=f'access_persons/{user["id"]}/accesses'
        )
        # ドアが1つなのでr[0]で良い
        schedule_id = r[0]['attributes']['access_schedule_id']
        # /schedules/:id
        r = self.__api(
            method='GET',
            path=f'schedules/{schedule_id}'
        )
        exception_id = r['attributes']['access_exception_id']
        r = self.__api(
            method='PUT',
            path=f'access_exceptions/{exception_id}',
            params={
                'attributes': {
                    'dates': exception_list
                }
            }
        )
        logger.info({
            'service': 'remotelock',
            'user': user['name'],
            'exception_count': len(exception_list)
        })

    def transform_rsv_time(self):
        rsvtime = self.rsv_info['rsv_time']
        m = re.match(r'([0-9]+)/([0-9]+)/([0-9]+) ([0-9]+):([0-9]+)[^0-9]+([0-9]+):([0-9]+)', rsvtime)
        (year, month, day, s_hour, s_min, e_hour, e_min) = m.groups()

        # 開始時間にn分のバッファを持たせるためのロジック
        starts_at_datetime = datetime.datetime(int(year), int(month), int(day), int(s_hour), int(s_min))
        remotelock_buffer_min:int = int(parameters.get_parameter('remotelock_buffer_min'))
        td_buffer_min = datetime.timedelta(minutes=remotelock_buffer_min)
        starts_at_datetime -= td_buffer_min
        s_hour = str(starts_at_datetime.hour) # 日付が変更されることはないので hour と min だけ補正する
        s_min = str(starts_at_datetime.minute)

        starts_at = f'{int(year):02}-{int(month):02}-{int(day):02}T{int(s_hour):02}:{int(s_min):02}:00'
        ends_at = f'{int(year):02}-{int(month):02}-{int(day):02}T{int(e_hour):02}:{int(e_min):02}:00'
        return (starts_at, ends_at)

    def __make_guest_name(self) -> str:
        name = f"{self.registered_info['name']} <{self.rsv_info['visible_rsv_no']}> ({self.registered_info['block']}{self.registered_info['kumi']}"
        if self.registered_info['name'] != self.registered_info['member_name']:
            name += f" {self.registered_info['member_name']} 様方"
        name += ')'
        return name

    def __error(self, params, r:requests.Response):
        logger.error({
            'service': 'remotelock',
            'params': params,
            'status_code': r.status_code,
            'message': r.reason
        })

    def __api(self, path:str, params:dict[str, Any] = {}, method='POST') -> dict[str, Any]:
        token = self.__get_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'pplication/vnd.lockstate+json; version=1'
        }
        r = None
        if method == 'POST':
            r = requests.post(f'https://api.remotelock.jp/{path}', headers=headers, json=params)
            if r.status_code == 409:
                logger.warn({'service': 'remotelock', 'cause': 'duplication error', 'params': params})
            if r.status_code != 201 and r.status_code != 200:
                self.__error(params, r)
                raise ResponseError(r.status_code, r.reason)
        elif method == 'GET':
            r = requests.get(f'https://api.remotelock.jp/{path}', headers=headers, params=params)
            if r.status_code != 200:
                self.__error(params, r)
                raise ResponseError(r.status_code, r.reason)
        elif method == 'PUT':
            r = requests.put(f'https://api.remotelock.jp/{path}', headers=headers, json=params)
            if r.status_code != 200:
                self.__error(params, r)
                raise ResponseError(r.status_code, r.reason)
        else:
            raise RuntimeError(f'method must be POST or GET or PUT ({method})')
        if len(r.content) > 0:
            return r.json()['data']
        else:
            return None

    def __refresh_token(self, remotelock_token:dict[str, str]) -> dict[str, str]:
        logger.info("refresh token...")
        client_key = parameters.get_parameter('remotelock_clientkey', transform="json")
        client_id = client_key['client_id']
        client_secret = client_key['client_secret']
        refresh_token = remotelock_token['refresh_token']
        epoch_now = int(time.time())
        r = requests.post(
            url='https://connect.remotelock.jp/oauth/token',
            params={
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token'
        })
        res = json.loads(r.text)
        access_token = res['access_token']
        refresh_token = res['refresh_token']
        expires_at = int(epoch_now + res['expires_in'])
        ssm = boto3.client('ssm')
        ssm.put_parameter(
            Name='remotelock_token',
            Value=json.dumps({
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_at': expires_at
            }),
            Type='String',
            Overwrite=True
        )
        return parameters.get_parameter('remotelock_token', force_fetch=True, transform="json")

    def __get_token(self) -> dict[str, str]:
        remotelock_token = parameters.get_parameter('remotelock_token', transform="json")
        expires_at:int = int(remotelock_token['expires_at'])
        epoch_now:int = int(time.time())
        if expires_at <= epoch_now + 120: # 有効期限が切れているか、今から2分以内に有効期限が切れる
            remotelock_token = self.__refresh_token(remotelock_token)
        return remotelock_token['access_token']
