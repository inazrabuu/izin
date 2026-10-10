from fastapi import FastAPI, Request, Response

from izin_server.webhooks import verify

SECRET = "dev-webhook-secret"
app = FastAPI()
seen: set[str] = set()

@app.post("/hook")
async def hook(request: Request) -> Response:
  body = await request.body()
  if not verify(SECRET, request.headers, body):
    return Response(status_code=401)

  delivery_id = request.headers["Izin-Delivery-Id"]
  if delivery_id in seen:
    print(f"duplicate delivery {delivery_id}; ignored")
    return Response(status_code=200)

  seen.add(delivery_id)
  print(f"delivery {delivery_id}: {body.decode()}")
  return Response(status_code=204)