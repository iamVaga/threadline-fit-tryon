---
title: FitCheck Fit and Try-On
emoji: 🧵
colorFrom: pink
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# FitCheck Fit & Try-On (PS-13)

Women's kurta size recommendations, simulated try-on, and retailer return analysis. The shop and October evaluation use the same browser recommender. The shopper can search the supplied kurta catalogue by SKU and add a usual size, a kept purchase or optional measurements. Measurements stay in the tab.

## Run locally on Windows

From the project root in PowerShell, using Python 3.13:

```powershell
python -m venv .venv
$py = './.venv/Scripts/python.exe'
& $py -m pip install -r requirements.txt
curl.exe -fL -o pose_landmarker_lite.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task
curl.exe -fL -o selfie_multiclass_256x256.tflite https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/1/selfie_multiclass_256x256.tflite
$env:MOCK = '1'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
& $py -m uvicorn server:app --port 8000 --no-access-log
```

Open http://127.0.0.1:8000. The detector runs in mock mode too. Use a sharp, front-facing portrait photo with one consenting adult and clear space above the head and below both feet. JPG, PNG and WebP files under 12 MB are supported. Each file must be at most 25 megapixels, and the photo's short side must be at least 480 pixels. A mock preview returns the original with a burned-in placeholder notice and simulation label.

## Controlled model evaluation

The live Hugging Face Space advertises `/tryon` with `dict`, `garm_img`, `garment_des`, `is_checked`, `is_checked_crop`, `denoise_steps`, and `seed`. The adapter downloads the first returned image and keeps both downloads in a short-lived local workspace. The alternate backend uses the pinned Replicate IDM-VTON model with `human_img`, `garm_img`, and `garment_des`.

Use a local loopback server and explicitly consented test assets. Confirm the person separately agrees to the named processor and its retention before evaluating. For a controlled HF Space, set `HF_MODEL_SPACE` to its URL or Space ID. Load `HF_TOKEN` into your terminal environment through your secret manager.

```powershell
Remove-Item Env:MOCK -ErrorAction SilentlyContinue
$env:TRYON_BACKEND = 'hf'
$env:TRYON_REMOTE = '1'
& $py -m uvicorn server:app --host 127.0.0.1 --port 8000 --no-access-log
```

For Replicate, use `TRYON_BACKEND=replicate` and load `REPLICATE_API_TOKEN` instead. Replicate charges for predictions and documents one-hour retention for API predictions. Review the model's non-commercial license and account billing before using it. Provider failures, timeouts, or failed clothing checks return a labeled placeholder. Measured latency covers model preparation, inference and postprocessing, excluding upload and the initial pose check. The API reports zero cost in explicit mock mode and null when provider billing is unknown.

The [public IDM-VTON schema](https://yisol-idm-vton.hf.space/info) and [pinned Replicate model](https://replicate.com/cuuupid/idm-vton/versions/d73e611d73581af0410fc0b5cc400e2cae959a92cb6c8b54d6ad102c52b4d5ab/api) document the inputs. See [Replicate retention](https://replicate.com/docs/topics/predictions/data-retention) for upstream cleanup behavior.

## Public Hugging Face Docker demo

Use these commands with a Hugging Face account eligible to create Docker Spaces. The authenticated attempt under `iamvaga` returned HTTP 402 and required PRO for Docker on CPU Basic. Replace the namespace and choose an unused Space name. The allowlist excludes team photos, workbooks, secrets, and local test artifacts.

```powershell
& $py -m pip install huggingface_hub==2.4.0
$hf = './.venv/Scripts/hf.exe'
& $hf auth login
& $hf auth whoami
$space = 'YOUR_NAMESPACE/fitcheck-fit-tryon'
& $hf repos create $space --type space --sdk docker --public
& $hf spaces variables add $space -e MOCK=1
& $hf upload $space . . --repo-type space --include README.md --include Dockerfile --include requirements.txt --include server.py --include tryon_model.py --include .dockerignore --include 'static/**'
& $hf spaces info $space
```

Open the Space's **App** tab and copy the running app's `.hf.space` URL after the build. The same URL should return HTTP 200 at `/` and an array at `/api/garments`.

The Docker demo defaults to local previews. Remote inference is a team-only hackathon demo for consenting adults. Load a long random `TRYON_TEAM_CODE` into the server environment for local evaluation.

### Render fallback

Push the app to a Git repository accessible to Render. In the [Render Web Service setup](https://dashboard.render.com/web/new), select that repo and its deployment branch, choose Docker, leave the Dockerfile path at `./Dockerfile`, and set `MOCK=1`. Render supplies `PORT` and the Dockerfile uses it. Once the service is live, check the root and `/api/garments` on its public URL. A prebuilt image is also supported through the Dashboard.

For the team-only Hugging Face demo, set `MOCK=0`, `TRYON_REMOTE=1`, `HF_TOKEN` to your inference token, and `TRYON_TEAM_CODE` to a long random access code in Render's Environment settings. Share the access code only with consenting adult team members. `TRYON_BACKEND` defaults to `hf`. Keep the token and code out of Git. Use team photos only and keep shopper photo forwarding disabled.

## Sample kurtas

The shop has four demo photos and the synthetic PS-13 kurta catalogue. Catalogue entries without a matching product photo accept a garment upload. Use clean flat-lay or mannequin images with permission to use them for try-on.

## Reproduce the size benchmark

```powershell
& $py train_fit.py "path/to/PS-13/Data"
node evaluate_fit.mjs
```

Training uses mature kept and exchanged outcomes before October. The browser receives fitted coefficients, catalogue data and five synthetic examples from the brief. Raw workbooks and per-case audit files stay in local storage. The evaluator scores all 2,841 cases through the same engine and writes the public aggregate results and a private per-case CSV under `.local/`. The evidence labels show the strength of available inputs. They are qualitative.

## Evaluation workbook

Keep the supplied `PS-13_fit_test_set.xlsx` unchanged. Create a private CSV with columns `case_id,photo,garment`, one row per selected grid case. Keep the consented person photos in `photos/` and the evaluation garments in a private directory. Use the relevant local folder paths with `--photos` and `--garments`.

```powershell
& $py eval_grid.py --workbook "path/to/PS-13_fit_test_set.xlsx" --assets-csv .local/tryon_assets.csv --photos photos --garments .local/garments --confirm-consented-adults
```

This writes `PS-13_fit_test_set_rendered.xlsx`. The helper records successful request latency through receipt of the image response and prints where the preview is stored. A human reviewer scores image quality and checks the label. Failed attempts are recorded in Notes. To fill verified billing costs, pass `--measured-cost-csv costs.csv` with columns `worksheet_row,cost_per_render_inr` and one entry per charged case.

## Privacy and limits

- Photo processing requires separate consent and a self-declared age of 18+. Measurement consent gates client-side size advice. Measurement values never go to the server.
- The server retains only the latest labeled preview for each session, removes it on deletion, and sweeps expired previews after 24 hours on a one-minute cycle. An active render and deletion are serialized. The API marks private responses as noncacheable.
- Photo checks and clothing segmentation run locally. The size engine trains offline on synthetic order data. The app's render metrics CSV holds latency, known cost, and the mock flag. Run a single worker for consistent preview limits and revocation of deleted sessions.
- The pose and clothing models can misclassify images. The clothing check copies generated pixels only where both images show high-confidence clothing, with a margin at the edges. Absolute preservation of face, skin tone, and body shape has not been established. This constraint can also reduce the fidelity of longer kurtas.
- FitCheck deletes the copies it controls. Hugging Face may retain or log inputs and does not expose a verified per-upload deletion contract. Replicate's API retention does not imply deletion when FitCheck's button is pressed. The team demo uses explicit consent to this limitation. A shopper rollout requires a controlled inference service that deletes all copies on request, prohibits training, and validates the protected regions.
- Provider billing, model quality and protected-region preservation need verification using the consented evaluation grid.

## Local verification

- Real HTTP requests to Uvicorn passed for the home page, garment listing, consent and age enforcement, photo rejection, three labeled placeholders, the fourth-request limit, and deletion. Photo rejections included small, landscape, empty, cropped, occluded, and two-person fixtures.
- A shortened retention interval verified automatic deletion. Without a provider token, the endpoint returned a labeled placeholder with unknown cost.
- Chromium at 380 CSS pixels verified all three tabs, the age and consent controls, and the placeholder and delete flow.
- A disposable workbook verified that the evaluation helper preserves the input workbook and leaves metric and human score fields blank when a case returns a placeholder.
