import os
import json
import time
import boto3
import pickle
import pandas as pd
from datetime import datetime
from botocore.config import Config
from botocore.exceptions import ClientError
from const_config import AzureCollector, Storage
import slack_msg_sender

STORAGE_CONST = Storage()
AZURE_CONST = AzureCollector()

session = boto3.session.Session(region_name='us-west-2')
write_client = session.client('timestream-write',
                              config=Config(read_timeout=20,
                                max_pool_connections=5000,
                                retries={'max_attempts': 10})
                              )



# Submit Batch To Timestream
def submit_batch(records, counter, recursive):
    if recursive == 10:
        return
    try:
        result = write_client.write_records(DatabaseName=STORAGE_CONST.BUCKET_NAME, TableName=STORAGE_CONST.AZURE_TABLE_NAME, Records=records,CommonAttributes={})

    except write_client.exceptions.RejectedRecordsException as err:
        slack_msg_sender.send_slack_message(err)
        print(err)
        re_records = []
        for rr in err.response["RejectedRecords"]:
            re_records.append(records[rr["RecordIndex"]])
        submit_batch(re_records, counter, recursive + 1)
        exit()
    except Exception as err:
        slack_msg_sender.send_slack_message(err)
        print(err)
        exit()


# Check Database And Table Are Exist and Upload Data to Timestream
def upload_timestream(data, timestamp):
    data = data[['InstanceTier', 'InstanceType', 'Region', 'OndemandPrice', 'SpotPrice', 'IF']]
    data['OndemandPrice'] = data['OndemandPrice'].fillna(-1)
    data['SpotPrice'] = data['SpotPrice'].fillna(-1)
    data['IF'] = data['IF'].fillna(-1)

    time_value = time.strptime(timestamp.strftime("%Y-%m-%d %H:%M"), '%Y-%m-%d %H:%M')
    time_value = time.mktime(time_value)
    time_value = str(int(round(time_value * 1000)))

    records = []
    counter = 0
    for idx, row in data.iterrows():

        dimensions = []
        for column in ['InstanceTier', 'InstanceType', 'Region']:
            dimensions.append({'Name': column, 'Value': str(row[column])})

        submit_data = {
            'Dimensions': dimensions,
            'MeasureName': 'azure_values',
            'MeasureValues': [],
            'MeasureValueType': 'MULTI',
            'Time': time_value
        }

        for column, types in [('OndemandPrice', 'DOUBLE'), ('SpotPrice', 'DOUBLE'), ('IF', 'DOUBLE')]:
            submit_data['MeasureValues'].append({'Name': column, 'Value': str(row[column]), 'Type': types})

        records.append(submit_data)
        counter += 1
        if len(records) == 100:
            submit_batch(records, counter, 0)
            records = []

    if len(records) != 0:
        submit_batch(records, counter, 0)


def update_latest(data, timestamp):
    data['id'] = data.index + 1
    data = data[['id', 'InstanceTier', 'InstanceType', 'Region', 'OndemandPrice', 'SpotPrice', 'Savings', 'IF']]
    data['OndemandPrice'] = data['OndemandPrice'].fillna(-1)
    data['Savings'] = data['Savings'].fillna(-1)
    data['IF'] = data['IF'].fillna(-1)

    data['time'] = datetime.strftime(timestamp, '%Y-%m-%d %H:%M:%S')

    result = data.to_json(f"{AZURE_CONST.SERVER_SAVE_DIR}/{AZURE_CONST.LATEST_FILENAME }", orient='records')

    session = boto3.Session()
    s3 = session.client('s3')

    with open(f"{AZURE_CONST.SERVER_SAVE_DIR}/{AZURE_CONST.LATEST_FILENAME }", 'rb') as f:
        s3.upload_fileobj(f, STORAGE_CONST.BUCKET_NAME, AZURE_CONST.S3_LATEST_DATA_SAVE_PATH)

    s3 = boto3.resource('s3')
    object_acl = s3.ObjectAcl(STORAGE_CONST.BUCKET_NAME, AZURE_CONST.S3_LATEST_DATA_SAVE_PATH)
    response = object_acl.put(ACL='public-read')

    pickle.dump(data, open(f"{AZURE_CONST.SERVER_SAVE_DIR}/{AZURE_CONST.SERVER_SAVE_FILENAME}", "wb"))


def save_raw(data, timestamp):
    data.to_csv(f"{AZURE_CONST.SERVER_SAVE_DIR}/{timestamp}.csv.gz", index=False, compression="gzip")

    session = boto3.Session()
    s3 = session.client('s3')

    s3_dir_name = timestamp.strftime("%Y/%m/%d")
    s3_obj_name = timestamp.strftime("%H-%M-%S")

    with open(f"{AZURE_CONST.SERVER_SAVE_DIR}/{timestamp}.csv.gz", 'rb') as f:
        s3.upload_fileobj(f, STORAGE_CONST.BUCKET_NAME, f"""rawdata/azure/{s3_dir_name}/{s3_obj_name}.csv.gz""")
    os.remove(f"{AZURE_CONST.SERVER_SAVE_DIR}/{timestamp}.csv.gz")

def query_selector(data):
    filename = 'query-selector-azure.json'
    s3_path = f'query-selector/{filename}'
    s3 = session.resource('s3')
    prev_selector_df = pd.DataFrame(json.loads(s3.Object(STORAGE_CONST.BUCKET_NAME, s3_path).get()['Body'].read()))
    selector_df = pd.concat([prev_selector_df[['InstanceTier', 'InstanceType', 'Region']], data[['InstanceTier', 'InstanceType', 'Region']]], axis=0, ignore_index=True).dropna().drop_duplicates(['InstanceTier', 'InstanceType', 'Region']).reset_index(drop=True)
    result = selector_df.to_json(f"{AZURE_CONST.SERVER_SAVE_DIR}/{filename}", orient='records')
    s3 = session.client('s3')
    with open(f"{AZURE_CONST.SERVER_SAVE_DIR}/{filename}", "rb") as f:
        s3.upload_fileobj(f, STORAGE_CONST.BUCKET_NAME, s3_path)
    os.remove(f"{AZURE_CONST.SERVER_SAVE_DIR}/{filename}")
    s3 = session.resource('s3')
    object_acl = s3.ObjectAcl(STORAGE_CONST.BUCKET_NAME, s3_path)
    response = object_acl.put(ACL='public-read')
