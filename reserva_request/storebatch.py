from report import get_all_registered_users
from aws_lambda_powertools.utilities import parameters
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from typing import Any
from util import ret_json, error_json
from remotelock import RemoteLock
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import calendar
import pandas as pd
import boto3
import botocore

logger = Logger()

COLUMNS = ["slot_start", "slot_end", "user_email", "block", "kumi", "official_flag", "external_flag", "user_name", "guest_name", "objective"]


def find_member(pre_registered_users, pre_registered_members, guest):
    email = guest["email"]
    if not email in pre_registered_users:
        raise RuntimeError(f"Cannot find member for {email}")
    user = pre_registered_users[email]
    member = pre_registered_members[user["member_id"]]
    return user, member


def make_row(pre_registered_users, pre_registered_members, access_users, access_guests, slot):
    slot_start = slot["start_time_iso"]
    slot_end = slot["end_time_iso"]
    user_email = ""
    block = ""
    kumi = ""
    official_flag = ""
    external_flag = ""
    user_name = ""
    guest_name = ""
    objective = ""

    for user in access_users:
        for user_time_slot in user["timeslots"]:
            if user_time_slot["start_time_iso"] == slot_start and user_time_slot["end_time_iso"] == slot_end:
                user_email = user["email"]
                block = ""
                kumi = ""
                official_flag = str(True)
                external_flag = str(False)
                user_name = user["name"]
                guest_name = ""
                objective = "定期予約"
                break

    for guest in access_guests:
        for guest_time_slot in guest["timeslots"]:
            if guest_time_slot["start_time_iso"] == slot_start and guest_time_slot["end_time_iso"] == slot_end:
                user, member = find_member(pre_registered_users, pre_registered_members, guest)
                user_email = guest["email"]
                block = member["block"]
                kumi = member["kumi"]
                official_flag = str(kumi == "公認団体")
                external_flag = str(False)
                user_name = member["member_name"]
                guest_name = guest["name"]
                objective = user["objective"]
                break

    return [slot_start, slot_end, user_email, block, kumi, official_flag, external_flag, user_name, guest_name, objective]


def make_used_data_pkl(target_year: int, target_month: int, pre_registered_users: dict[str, Any], pre_registered_members: dict[str, Any], remotelock: RemoteLock):
    start_day: datetime = datetime(target_year, target_month, 1)

    access_users = remotelock.get_users(start_day)
    access_guests = remotelock.get_access_guests(target_year, target_month)

    times = [("05:00", "09:00"), ("09:00", "13:00"), ("13:00", "17:00"), ("17:00", "21:00")]
    df = pd.DataFrame(index=[], columns=COLUMNS)
    for day in range(calendar.monthrange(target_year, target_month)[1]):
        dt = date(target_year, target_month, day + 1)
        dt_iso = dt.isoformat()
        for time in times:
            start_time, end_time = time
            slot = remotelock.make_slot(dt.strftime("%Y-%m-%d"), dt_iso, start_time, end_time)
            df.loc[f"{day}-{times.index(time)+1}"] = make_row(pre_registered_users, pre_registered_members, access_users, access_guests, slot)

    logger.info(f"{len(pre_registered_users)} registered users, {len(pre_registered_members)} registered members, {len(access_guests)} access guests, {len(access_users)} access users.")

    file_name = f"{target_year}-{target_month:02d}.pkl"
    local_file_name = f"/tmp/{file_name}"
    s3_key = f"used_data_pkl/{file_name}"
    df.to_pickle(f"/tmp/{file_name}")
    s3 = boto3.resource("s3")
    s3bucket = s3.Bucket(parameters.get_parameter("reserva_bucket_info"))
    s3bucket.upload_file(local_file_name, s3_key)
    logger.info(f"pkl file {s3_key} uploaded.")


def check_used_data_pkl(target_year: int, target_month: int):
    today = date.today()
    if today.year == target_year and today.month == target_month:
        return True

    file_name = f"{target_year}-{target_month:02d}.pkl"
    s3_key = f"used_data_pkl/{file_name}"
    s3 = boto3.resource("s3")
    s3bucket = s3.Bucket(parameters.get_parameter("reserva_bucket_info"))
    try:
        s3bucket.Object(s3_key).load()
        logger.info(f"pkl file exists.")
        return False
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return True
        else:
            raise


@logger.inject_lambda_context(log_event=True)
def handler(event: dict, context: LambdaContext) -> dict[str, Any]:
    remotelock = RemoteLock()
    pre_registered_users, pre_registered_members = get_all_registered_users()

    thismonth_start = date.today().replace(day=1)
    for i in range(0, 24):
        target_day = thismonth_start - relativedelta(months=i)
        target_year = target_day.year
        target_month = target_day.month
        logger.info(f"target_year={target_year}, target_month={target_month}")
        if check_used_data_pkl(target_year, target_month):
            make_used_data_pkl(target_year, target_month, pre_registered_users, pre_registered_members, remotelock)
        else:
            break

    return ret_json(200, {"message": "hello world"})
