import os
import sys
import requests

# 환경변수 로드
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_DATABASE_ID = os.environ.get("CF_DATABASE_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()

# 필수 값 검증
if not all([CF_ACCOUNT_ID, CF_DATABASE_ID, CF_API_TOKEN]):
    print("❌ 에러: CF_ACCOUNT_ID, CF_DATABASE_ID, CF_API_TOKEN 중 하나 이상이 설정되지 않았습니다.")
    sys.exit(1)

# Cloudflare D1 REST API URL (표준 Raw Query 엔드포인트)
url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/raw"

headers = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

# 테스트용 샘플 데이터
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

# SQL 쿼리 생성
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

full_sql = " ".join(sql_statements)
payload = {"sql": full_sql}

try:
    response = requests.post(url, headers=headers, json=payload)
    print("D1 응답 상태 코드:", response.status_code)
    print("D1 응답 결과:", response.text)
    
    if response.status_code != 200:
        sys.exit(1)
        
    print("🎉 D1 데이터베이스 업데이트 성공!")
except Exception as e:
    print("❌ 요청 중 에러 발생:", e)
    sys.exit(1)
