import os
import requests

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID")
CF_DATABASE_ID = os.environ.get("CF_DATABASE_ID")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")

# Cloudflare D1 REST API URL
url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/raw"

headers = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

# 1. 공공데이터 API 등에서 병원 데이터를 수집하는 예시 (샘플 데이터)
# 나중에 공공데이터포털 API Key를 받으시면 여기에 API 호출 로직을 연결하면 됩니다.
sample_hospitals = [
    {
        "id": "HOSP_001",
        "name": "서울중앙의원",
        "type": "의원",
        "address": "서울특별시 중구 세종대로 110",
        "phone": "02-123-4567",
        "latitude": 37.5665,
        "longitude": 126.9780,
        "is_silbi": 1
    },
    {
        "id": "VET_001",
        "name": "행복한동물병원",
        "type": "동물병원",
        "address": "경기도 안산시 단원구 중앙대로 123",
        "phone": "031-987-6543",
        "latitude": 37.3172,
        "longitude": 126.8328,
        "is_silbi": 0
    }
]

# 2. D1에 UPSERT(있으면 업데이트, 없으면 추가) 쿼리 구문 작성
sql_statements = []
for h in sample_hospitals:
    sql = f"""
    INSERT INTO hospitals (id, name, type, address, phone, latitude, longitude, is_silbi, updated_at)
    VALUES ('{h['id']}', '{h['name']}', '{h['type']}', '{h['address']}', '{h['phone']}', {h['latitude']}, {h['longitude']}, {h['is_silbi']}, datetime('now'))
    ON CONFLICT(id) DO UPDATE SET
        name=excluded.name,
        type=excluded.type,
        address=excluded.address,
        phone=excluded.phone,
        latitude=excluded.latitude,
        longitude=excluded.longitude,
        is_silbi=excluded.is_silbi,
        updated_at=datetime('now');
    """
    sql_statements.append(sql)

# 3. D1 API 호출하여 DB에 반영
full_sql = " ".join(sql_statements)
payload = {"sql": full_sql}

response = requests.post(url, headers=headers, json=payload)
print("D1 응답 상태:", response.status_code)
print("D1 응답 결과:", response.text)
