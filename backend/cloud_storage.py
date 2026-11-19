"""Private branch-scoped object storage. Only call after workspace authorization."""
import uuid

BUCKET='gtfs-workbench-private'

def object_key(workspace,feed,kind):
    workspace=uuid.UUID(str(workspace));feed=uuid.UUID(str(feed))
    if kind not in ('source','output'):raise ValueError('Invalid object kind')
    return f'workspaces/{workspace}/feeds/{feed}/{kind}-{uuid.uuid4()}.zip'

def presign_upload(client,key,expires=300):
    return client.generate_presigned_url('put_object',Params={'Bucket':BUCKET,'Key':key,'ContentType':'application/zip'},ExpiresIn=expires,HttpMethod='PUT')

def presign_download(client,key,expires=300):
    return client.generate_presigned_url('get_object',Params={'Bucket':BUCKET,'Key':key},ExpiresIn=expires,HttpMethod='GET')

def download(client,key,fileobj):
    client.download_fileobj(BUCKET,key,fileobj)
    fileobj.seek(0)
