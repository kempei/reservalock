from aws_lambda_powertools.utilities import parameters

from typing import Any
import json
import gspread
from datetime import datetime


def ret_json(status_code: int, json_dict: dict) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json;charset=UTF-8"},
        "body": json.dumps(json_dict, ensure_ascii=False)
    }


def error_json(title: str, message: str) -> dict[str, Any]:
    return ret_json(400, {'title': title, 'message': message})


class GSpreadsheetUtil:
    @classmethod
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
    @classmethod
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
