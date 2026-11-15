from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

import server
import tryon_model
from gpu_worker import app as gpu_app
from recommender import model


def png(width=480, height=660):
    values = np.indices((height,width)).sum(axis=0) % 255
    image = Image.fromarray(values.astype("uint8")).convert("RGB")
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def upload():
    return {"photo": ("photo.png", png(), "image/png"),
            "garment": ("garment.png", png(), "image/png")}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TMP", tmp_path / "sessions")
    monkeypatch.setattr(server, "LOG", tmp_path / "render_log.csv")
    monkeypatch.setattr(server, "DAILY_LEDGER", tmp_path / "daily.json")
    monkeypatch.setattr(server, "COUNTS", [{} for _ in server.LOCKS])
    monkeypatch.setenv("MOCK","1")
    with TestClient(server.app) as test:
        yield test


def form():
    return {"session": str(uuid4()),"age":"18","consent_photo":"true"}


@pytest.mark.parametrize("updates",[{"age":"17"},{"consent_photo":"false"}])
def test_consent_age(client,upload,updates):
    response=client.post("/api/tryon",data=form()|updates,files=upload)
    assert response.status_code==403
    assert client.get("/api/metrics").json()["attempts"]==0


def test_session_global_cap_and_delete(client,upload,monkeypatch):
    monkeypatch.setattr(server,"check_photo",lambda _:None)
    monkeypatch.setattr(server,"DAILY_CAP",4)
    session=form()
    for i in range(3):
        result=client.post("/api/tryon",data=session,files=upload)
        assert result.status_code==200
        assert result.json()["renders_used"]==i+1
        assert result.json()["label"]==server.LABEL
        assert result.json()["mock"] is True
    assert client.post("/api/tryon",data=session,files=upload).status_code==429
    assert (server.TMP/session["session"]/"result.png").is_file()
    assert client.post("/api/delete",data={"session":session["session"]}).json()=={"deleted":True}
    assert not (server.TMP/session["session"]).exists()
    assert client.post("/api/tryon",data=session,files=upload).status_code==410
    assert client.post("/api/tryon",data=form(),files=upload).status_code==200
    assert client.post("/api/tryon",data=form(),files=upload).status_code==429
    stats=client.get("/api/metrics").json()
    assert stats["attempts"]==4 and stats["renders"]==0 and stats["p95_latency_s"] is None


def test_failed_provider_shows_gallery_error(client,upload,monkeypatch):
    monkeypatch.setattr(server,"check_photo",lambda _:None)
    monkeypatch.setattr(server,"run_model",lambda *args:(args[0],"model_unavailable"))
    response=client.post("/api/tryon",data=form(),files=upload)
    assert response.status_code==503
    assert "sample gallery" in response.json()["error"] and not response.json().get("image")


def test_tiny_and_dark_rejected_before_renderer(client,upload,monkeypatch):
    monkeypatch.setattr(server,"run_model",lambda *args:pytest.fail("model contacted"))
    black=BytesIO()
    Image.new("RGB",(480,660)).save(black,"PNG")
    for fixture in (png(32,50),black.getvalue()):
        response=client.post("/api/tryon",data=form(),files=upload|{"photo":("fixture.png",fixture)})
        assert response.status_code==422 and response.json()["rejected_before_render"]
    assert not server.DAILY_LEDGER.exists()


@pytest.mark.parametrize("crop,people", [("head",1),("feet",1),(None,2)])
def test_pose_boundary_contract(client,upload,monkeypatch,crop,people):
    # These geometries exercise policy independently from the pose model's image recognition.
    landmarks = [SimpleNamespace(x=.5,y=.5,visibility=.95,presence=.95) for _ in range(33)]
    foreground = np.zeros((660,480),dtype=np.float32)
    foreground[40:620,180:310]=1
    if crop=="head":
        foreground[:60,215:275]=1
    elif crop=="feet":
        foreground[590:,220:258]=1
    output = SimpleNamespace(pose_landmarks=[landmarks]*people,
                             segmentation_masks=[SimpleNamespace(numpy_view=lambda:foreground)])
    class Detector:
        def __enter__(self):
            return self
        def __exit__(self,*args):
            return None
        def detect(self,_):
            return output
    monkeypatch.setattr(tryon_model.mp.tasks.vision.PoseLandmarker,"create_from_options",lambda _:Detector())
    monkeypatch.setattr(server,"run_model",lambda *args:pytest.fail("model contacted"))
    response=client.post("/api/tryon",data=form(),files=upload)
    assert response.status_code==422 and response.json()["rejected_before_render"]
    assert not server.DAILY_LEDGER.exists()


def test_binary_composite_and_label_preserve_original_pixels():
    original=Image.new("RGB",(55,70),(1,2,3))
    modified=Image.new("RGB",original.size,(20,30,40))
    mask=Image.new("L",original.size)
    mask.paste(255,(7,10,39,62))
    mask.putpixel((1,1),100)
    generated=tryon_model.composite_preserving_source(original,modified,mask)
    published=Image.open(BytesIO(server.stamp(generated,False))).crop((0,0,*original.size))
    outside=np.asarray(mask)!=255
    assert np.array_equal(np.asarray(published)[outside],np.asarray(original)[outside])
    assert published.getpixel((8,11))==(20,30,40)


def test_gemini_is_opt_in_and_worker_auth(monkeypatch,upload):
    monkeypatch.setenv("MOCK","0")
    monkeypatch.setenv("TRYON_REMOTE","1")
    monkeypatch.setenv("TRYON_BACKEND","gemini")
    monkeypatch.delenv("ALLOW_GEMINI_PHOTOS",raising=False)
    assert tryon_model.remote_backend() is None
    monkeypatch.delenv("TRYON_BACKEND")
    assert tryon_model.remote_backend()=="local_http"
    with TestClient(gpu_app) as client:
        result=client.post("/render",files={"person":upload["photo"],"garment":upload["garment"]})
    assert result.status_code==401


@pytest.mark.parametrize("case",["FT-10022","FT-10001","FT-10025","FT-10024","FT-10026"])
def test_recommend_scenarios_return_reproducible_predictions(client,case):
    observed=client.post("/api/recommend",json={"test_id":case})
    assert observed.status_code==200
    result=observed.json()
    expected=model()["predictions"][case]
    assert all(result[k]==expected[k] for k in result)
    assert result["size"] in model()["products"][result["sku_id"]]["sizes_available"]
    assert result["reason"] and 0<=result["confidence"]<=1


def test_recommend_numeric_manual_and_measurement_consent(client):
    params={"sku_id":"TL-102361","usual_size":"32"}
    plain=client.post("/api/recommend",json=params).json()
    assert plain["size"].isdigit() and "no stretch" in plain["reason"]
    assert client.post("/api/recommend",json=params|{"waist":130}).json()==plain


def test_chart_evidence_and_all_catalogue_categories(client):
    result=client.get("/api/fit-results").json()
    brands=result["brands"]
    assert len(brands)==8 and result["overall"]["n"]==2841
    assert all(type(x["chart_unreliable"]) is bool for group in brands.values() for x in group.values())
    lane=brands["Women · Kurtas"]["Chandni Lane"]
    assert lane["chart_n"]>=15 and lane["gap"] is not None and lane["chart_unreliable"]
    assert client.get("/api/catalog").json()[0]["sku_id"].startswith("TL-")
