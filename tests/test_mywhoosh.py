import json

import pytest
import responses

from bridge.mywhoosh import ACTIVITIES_URL, DOWNLOAD_URL, LOGIN_URL, Activity, MyWhooshClient, MyWhooshError

LOGIN_OK = {"Success": True, "AccessToken": "tok", "WhooshId": "wid"}


@responses.activate
def test_recent_activities_logs_in_and_skips_rides_without_files():
    responses.post(LOGIN_URL, json=LOGIN_OK)
    responses.post(ACTIVITIES_URL, json={"data": {"results": [
        {"id": 7, "activityFileId": "f7", "name": "Ride"},
        {"_id": "8", "activityFileId": None},
    ]}})
    assert MyWhooshClient("e", "p").recent_activities() == [Activity("7", "f7", "Ride")]
    assert responses.calls[1].request.headers["Authorization"] == "Bearer tok"
    assert json.loads(responses.calls[1].request.body)["sortDate"] == "DESC"


@responses.activate
def test_expired_token_logs_in_again():
    responses.post(LOGIN_URL, json=LOGIN_OK)
    responses.post(ACTIVITIES_URL, status=401)
    responses.post(ACTIVITIES_URL, json={"data": {"results": []}})
    assert MyWhooshClient("e", "p").recent_activities() == []
    assert [c.request.url for c in responses.calls].count(LOGIN_URL) == 2


@responses.activate
def test_failed_login_raises():
    responses.post(LOGIN_URL, json={"Success": False, "Message": "bad password"})
    with pytest.raises(MyWhooshError, match="bad password"):
        MyWhooshClient("e", "p").recent_activities()


@responses.activate
def test_download_follows_presigned_url():
    responses.post(LOGIN_URL, json=LOGIN_OK)
    responses.post(DOWNLOAD_URL, json={"data": "https://s3.example/ride.fit"})
    responses.get("https://s3.example/ride.fit", body=b"FITDATA")
    assert MyWhooshClient("e", "p").download(Activity("7", "f7", "Ride")) == b"FITDATA"
    assert json.loads(responses.calls[1].request.body) == {"key": "wid", "fileId": "f7"}


@responses.activate
def test_download_without_url_raises():
    responses.post(LOGIN_URL, json=LOGIN_OK)
    responses.post(DOWNLOAD_URL, json={"error": True, "message": "nope"})
    with pytest.raises(MyWhooshError):
        MyWhooshClient("e", "p").download(Activity("7", "f7", "Ride"))
