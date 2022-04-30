import requests
import json
import boto3 # You can specify non-default profile using AWS_PROFILE environment
import sys

print("getting client key...")

ssm = boto3.client('ssm')
client_key = json.loads(ssm.get_parameter(Name='remotelock_clientkey')['Parameter']['Value'])
client_id = client_key['client_id']
client_secret = client_key['client_secret']

if len(sys.argv) < 2:
  print("Usage: setup.py <authorization code>")
  print("To get authorization code, access the below URL.")
  print(f"https://connect.remotelock.jp/oauth/authorize?client_id={client_id}&redirect_uri=urn:ietf:wg:oauth:2.0:oob&response_type=code")
  sys.exit(0)

authorization_code = sys.argv[1]

print("getting access token...")

r = requests.post(
  url='https://connect.remotelock.jp/oauth/token',
  params={
    'code': authorization_code,
    'client_id': client_id,
    'client_secret': client_secret,
    'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
    'grant_type': 'authorization_code'
  })

res = json.loads(r.text)
print(res)

access_token = res['access_token']
refresh_token = res['refresh_token']
expires_at = int(res['created_at'] + res['expires_in'])

val = {
  'access_token': access_token,
  'refresh_token': refresh_token,
  'expires_at': expires_at
}

print("putting access token / refresh token...")

ssm.put_parameter(Name='remotelock_token', Value=json.dumps(val), Type='String', Overwrite=True)

print('completed.')