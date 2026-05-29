"""发票通 (ivs.fapiao.com) API integration.

- sign:   HMAC-SHA256 canonical-string signing (Aliyun-style)
- client: synchronous httpx-based API client
- parser: decode base64 JSON response into flat rows
- ingest: upsert parsed rows into Postgres warehouse
"""
