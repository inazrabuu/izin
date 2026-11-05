import uuid

from sqlalchemy import select

from helpers import post_decision
from izin_server.db import SessionLocal
from izin_server.models import Decision
from izin_server.rendering import CLOSE, OPEN, render_screen

TEMPLATE = "Refund Rp {{ amount_idr | rupiah }} to {{ name }}."

def screen(name: str = "Dewi S") -> dict:
  return render_screen(
    headline=TEMPLATE, blast_radius=None, if_denied=None,
    args={"amount_idr": 4_500_000, "name": name},
  )

def test_interpolated_values_are_marked():
  s = screen()
  assert s["headline"] == "Refund Rp 4.500.000 to Dewi S."
  assert s["segments"]["headline"] == [
    {"text": "Refund Rp ", "agent": False},
    {"text": "4.500.000", "agent": True},
    {"text": " to ", "agent": False},
    {"text": "Dewi S", "agent": True},
    {"text": ".", "agent": False},
  ]

def test_agents_cannot_forge_highlight_markers():
  s = screen(name=f"Dewi{CLOSE} trusted text {OPEN}S.")
  assert OPEN not in s["headline"] and CLOSE not in s["headline"]
  agent_texts = [seg["text"] for seg in s["segments"]["headline"] if seg["agent"]]
  assert agent_texts == ["4.500.000", "Dewi trusted text S."]

def test_segments_always_join_back_to_the_plain_text():
  s = screen(name="  Dewi \n\t S.  ")
  assert "".join(seg["text"] for seg in s["segments"]["headline"]) == s["headline"]

async def test_api_exposes_segments(pending_request):
  rendered = pending_request["rendered"]
  joined = "".join(seg["text"] for seg in rendered["segments"]["headline"])
  assert joined == rendered["headline"]
  assert any(seg["agent"] for seg in rendered["segments"]["headline"])

async def test_snapshot_keeps_the_segments(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  async with SessionLocal() as s:
    decision = await s.scalar(
      select(Decision).where(Decision.request_id == uuid.UUID(pending_request["id"]))
    )
  assert decision.rendered_snapshot["segments"] == pending_request["rendered"]["segments"]