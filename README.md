# reserva-request-lambda

## 概要

一連のプログラムは、Reserva サイトと、RemoteLock API を相互に制御するものです。次の機能を持ちます。

### GAS

* GMail にて5分に一度 Reserva から届いたスター付きのメールを検索し、Lambda で実装された API を呼び出します。

### ReservaRequest Lambda

* 予約申請時に Reserva から送付される URL を元にメールアドレスを Google Spreadsheet 上の事前登録マスタと照合し、登録済みのメールアドレスからの予約のみ承諾し、その他は拒否します。
  * 承諾された予約については、当該時間帯のみアクセス可能なアクセスゲストが RemoteLock API にて生成され、鍵番号が予約ユーザに通知されます。
* 予約キャンセル時には、Reserva から送付される URL を元に RemoteLock のアクセスゲストを特定し、削除します。

### CreateAccess Lambda

* 週に一度、RemoteLock のアクセスユーザをスキャンし、定期的な予定について Reserva に予約を作成すると共に、RemoteLock にはアクセス不可日を設定します。

# GAS

以下のサイトを参考にしています。

https://www.lisz-works.com/entry/gas-recv-gmail-check

https://valmore.work/how-to-copy-gmail-message-to-spreadsheet/

https://developers.google.com/apps-script/reference/url-fetch/url-fetch-app#fetch(String,Object)

https://journal.lampetty.net/entry/call-external-api-in-gas

スターにする条件は別途 GMail の機能にて設定されています。

# テスト方法

```bash
$ . ./env
$ pytest
```

# デプロイ方法

```bash
$ sam build
$ sam deploy --profile <yourprofile>
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
