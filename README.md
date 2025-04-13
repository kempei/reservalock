# reserva-request-lambda

## 概要

一連のプログラムは、町内会の公会堂予約を前提として、[Reserva](https://reserva.be/) と [RemoteLock](https://remotelock.kke.co.jp/) を相互に自動的に制御するものです。Reserva と RemoteLock には連携する機能が[存在](https://remotelock.kke.co.jp/api/reserva/)していますが、高価であり、機能も限定的です。この実装では、予約の承認と鍵番号振り出しの自動化、予約キャンセル時の鍵番号取り消しの自動化、町内会組織による定期的な予約の自動化、内外用の定期的なレポーティングの自動化を極力コストをかけない方法で実現しています。

### 前提

本実装は以下の前提に基づいています。

- Reserva によって1施設の予約を管理している。
- RemoteLock によって1つの鍵を管理している。
- RemoteLock の API が使用可能である。
- Gmail にて Reserva のEメールを受信している。
- Google Spreadsheet にて台帳を管理している。

## 各機能の説明

各機能の流れについて以下述べます。

### 予約の承認と鍵番号振り出し

ユーザによる予約がされた場合にすべきことは、予約の承認/拒否の判断と鍵番号の振り出しです。

予約を承認する条件は、予約をしたユーザが町内会員であることです。ユーザの身元を確認にするには、町内会員の名義を必要とします。しかし、Reserva の予約サイトの入力項目で必須なのはメールアドレスのみであり、不足しています。また、同一メールアドレスで複数の名前を名乗れること、どのブロック/組の町内会員と紐付いているかは家族の名前などからでは類推が難しいことなどもあり、Reserva 以外の台帳による確認が必要となります。そこで、別途台帳への事前登録の仕組みを用意し、台帳のデータと照合して身元を確認し、確認できた予約のみ承認するようにします。

仕組みとしては、町内会のEメールを [GAS](https://workspace.google.co.jp/intl/ja/products/apps-script/) によって 5分に一度チェックし、Reserva から届いた予約の Eメールがあれば情報を抽出し、[AWS Lambda Function URL](https://docs.aws.amazon.com/ja_jp/lambda/latest/dg/lambda-urls.html) を呼び出すことで実現します。AWS Lambda は、Reserva にアクセスして予約内容を確認し、ユーザのメールアドレスを Google Spreadsheet 上の台帳と照合します。当該メールアドレスが事前登録されたものであれば承認し、その他は拒否します。

予約の承認に伴い、鍵番号を振り出します。これは、予約時間のn分前(例: n=30)から有効となる RemoteLock のアクセスゲストを作成することで実現します。鍵番号が振り出されると、Reserva から鍵番号情報と共に Eメールにてユーザに通知されます。なお、RemoteLock の通知メール機能によって、ユーザには前日にも鍵番号を通知します。

#### 事前登録について

事前登録は町内会の回覧板にて配布した Google フォームから行います。登録結果は Google Spreadsheet に保存されます。

>要望として多いのが事前登録後に利用方法などを記したEメールを届けて欲しいというものです。開発を予定しています。

### 予約キャンセル時の鍵番号取り消し

ユーザによって予約がキャンセルされた際にすべきことは、対応する RemoteLock 側のアクセスゲストの削除です。

仕組みとしては、予約申請時と同様に、GAS にてキャンセルのための通知から情報を読み取り、AWS Lambda URL を呼び出します。Reserva にアクセスして予約者のメールアドレスを特定し、該当する RemoteLock のアクセスゲストを検索して、削除します。

### 町内会組織による定期的な予約

町内会組織に紐付いたイベントには定期的に公会堂を用いるものが多くあり、それらは都度予約をせずとも自動的に予約がされることで利便性が高まります。予約のパターンとしては、「第n週のx曜日午後」と、月ごとに週と曜日、時間が固定されています。町内会組織ごとに予約パターンを設定しておけば、自動的に未来の予約をし続けるようにすれば実現できます。

仕組みは少し複雑です。まず、町内会組織は、町内会の Eメールアドレスのエイリアスを用います。通常、Eメールアドレスは example@gmail.com のような形式ですが、example+alias@gmail.com のように + を途中に付加することで、異なるEメールアドレスを取得することなく、別名を定義することができます。組織毎にエイリアスを定義することで町内会の1つのEメールアドレスで複数の組織を表現することができるわけです。このエイリアス付きのEメールアドレスを使って町内会組織毎に RemoteLock のアクセスユーザを作成します。

予約パターンは、RemoteLock のアクセスユーザの属性データで表現します。こうすることで、外部データストアを必要とすることなく実装することが可能となり、RemoteLock の管理画面から追加/修正/削除が可能となります。アクセスユーザ毎に予約パターンは次のような `json` 形式で表現します。以下の例では`第1火曜日の 9:00-13:00枠 + 13:00-17:00枠、第3土曜日の 9:00-13:00枠 + 13:00-17:00枠`を予約するものです。曜日毎に第何週なのか、どの枠なのかを表現していることがわかります。

```:json
[
  {"day":"Tue", "slot":["09:00","13:00","13:00","17:00"], "week": [1]},
  {"day":"Sat", "slot":["09:00","13:00","13:00","17:00"], "week": [3]}
]
```

ただし残念ながら、RemoteLock にはこのパターンを API で一発で直接設定する方法はありません。その代わりに、`アクセススケジュール`を用いるとアクセスユーザがデフォルトでアクセスできる曜日毎の時間帯を設定できること、そして`アクセス不可日`によって個別にアクセス不可の日を設定できることを利用します。まず、上記の例では`火曜日と土曜日の 9:00-17:00`にアクセス可能な`アクセススケジュール`を作成します。そして、`アクセス不可日`に対して当該日**以外**を追加する、というようにします。こうすると、上記で設定している日時にのみアクセス可能という状態にすることができるのです。`アクセススケジュール`の作成とスケジュールの設定、`アクセス不可日`の作成(設定は不要)については予め RemoteLock の管理画面で実行しておく必要があります。

最後に、予約されたスロットにおいて Reserva の予約が自動的に実施します。

これらを毎週1回、半年後までのスロットに対して実行しておきます。なお、アクセスユーザを初めて設定した際の実行では Reserva の予約を半年後まで確保するために月ごとに設定された予約数を超える場合がありますので注意します。

### 定期的なレポーティング(外部用) (開発中)

公会堂を利用するすべての方に必要な情報を提供するものです。Reserva の予約サイトでは、埋まっている時間帯が予約の時間切れなのか、個人で確保しているものなのか、町内会組織が確保しているものなのかがわかりません。そこで、町内会組織によるものは組織名を表示し、個人のものは匿名として予約状態がわかる表を HTML として公開しておきます。これは予約サイトからも遷移できるようにし、ホームページからも見られるようにしておきます。

### 定期的なレポーティング(内部用) (開発中)

公会堂の管理者や理事の方向けに必要な情報として、次のリストに示す内容を記したレポートを提供します。

- 現在事前登録をされている方々のリストとその月の差分
- 個人ユーザごとの当該月の利用回数と請求料金
- 予約したがロック解除情報がなく利用がなかった枠とそのユーザのリスト
- 予約された枠数と利用率
- ロック解除に失敗した回数と日時



# GAS

以下のサイトを参考にしています。

https://www.lisz-works.com/entry/gas-recv-gmail-check

https://valmore.work/how-to-copy-gmail-message-to-spreadsheet/

https://developers.google.com/apps-script/reference/url-fetch/url-fetch-app#fetch(String,Object)

https://journal.lampetty.net/entry/call-external-api-in-gas

スターにする条件は別途 GMail の機能にて設定されています。


# SAM と Docker

Docker を新しくインストールした場合は以下が必要なことがある。

```:bash
sudo ln -s ~/.docker/run/docker.sock /var/run/docker.sock
```

# テスト方法

SAM のテストは以下。ファンクション名とイベント名、イベントの内容、プロファイルやリージョンは適宜変更のこと。

```bash
sam build
sam local invoke ReservaRequestFunction -e test1-event.json --profile default --region ap-northeast-1
```



ライブラリのテストは以下。

```bash
. ./env
pytest
```

# デプロイ方法

```bash
sam build
sam deploy --profile <yourprofile>
```

## Use the SAM CLI to build and test locally

Build your application with the `sam build --use-container` command.

```bash
reserva-request-lambda$ sam build --use-container
```

The SAM CLI installs dependencies defined in `hello_world/requirements.txt`, creates a deployment package, and saves it in the `.aws-sam/build` folder.

Test a single function by invoking it directly with a test event. An event is a JSON document that represents the input that the function receives from the event source. Test events are included in the `events` folder in this project.

Run functions locally and invoke them with the `sam local invoke` command.

```bash
reserva-request-lambda$ sam local invoke ReservaRequestFunction --event test1-event.json --profile private
```

The SAM CLI can also emulate your application's API. Use the `sam local start-api` to run the API locally on port 3000.

```bash
reserva-request-lambda$ sam local start-api
reserva-request-lambda$ curl http://localhost:3000/
```

The SAM CLI reads the application template to determine the API's routes and the functions that they invoke. The `Events` property on each function's definition includes the route and method for each path.

```yaml
      Events:
        HelloWorld:
          Type: Api
          Properties:
            Path: /hello
            Method: get
```

## Add a resource to your application


## Fetch, tail, and filter Lambda function logs

To simplify troubleshooting, SAM CLI has a command called `sam logs`. `sam logs` lets you fetch logs generated by your deployed Lambda function from the command line. In addition to printing the logs on the terminal, this command has several nifty features to help you quickly find the bug.

`NOTE`: This command works for all AWS Lambda functions; not just the ones you deploy using SAM.

```bash
reserva-request-lambda$ sam logs -n HelloWorldFunction --stack-name reserva-request-lambda --tail
```

You can find more information and examples about filtering Lambda function logs in the [SAM CLI Documentation](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-logging.html).

## Tests

Tests are defined in the `tests` folder in this project. Use PIP to install the test dependencies and run tests.

```bash
reserva-request-lambda$ pip install -r tests/requirements.txt --user
# unit test
reserva-request-lambda$ python -m pytest tests/unit -v
# integration test, requiring deploying the stack first.
# Create the env variable AWS_SAM_STACK_NAME with the name of the stack we are testing
reserva-request-lambda$ AWS_SAM_STACK_NAME=<stack-name> python -m pytest tests/integration -v
```

## Cleanup

To delete the sample application that you created, use the AWS CLI. Assuming you used your project name for the stack name, you can run the following:

```bash
aws cloudformation delete-stack --stack-name reserva-request-lambda
```

## Resources

See the [AWS SAM developer guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/what-is-sam.html) for an introduction to SAM specification, the SAM CLI, and serverless application concepts.

Next, you can use AWS Serverless Application Repository to deploy ready to use Apps that go beyond hello world samples and learn how authors developed their applications: [AWS Serverless Application Repository main page](https://aws.amazon.com/serverless/serverlessrepo/)
