# MSE Image Bulk Runner

Simple full-stack app to bulk-run image generations/edits using:

- multiple uploaded images
- multiple prompts (one prompt per line)
- cartesian product execution (`images x prompts`)

Example:

- 5 pictures
- 3 prompts

Creates 15 jobs:

- `Picture 1 + Prompt 1`
- `Picture 1 + Prompt 2`
- `Picture 1 + Prompt 3`
- `Picture 2 + Prompt 1`
- ...

## Features

- FastAPI backend + simple frontend
- Multiple image upload
- Multiple prompts in one batch
- Configurable model / size / quality / output format / concurrency
- Polling for job status
- Preview and download completed images

## Requirements

- Python 3.10+
- OpenAI API key with access to the Images API

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Set your API key in `.env` (created in the project root):

```powershell
OPENAI_API_KEY=your_api_key_here
```

Optional `.env` settings:

- `IMAGE_MODEL` (default: `gpt-image-1`)
- `OPENAI_BASE_URL` (default: `https://api.openai.com/v1`)

## Run

```powershell
uvicorn main:app --reload
```

Open:

- `http://127.0.0.1:8000`

## Notes

- The backend stores uploaded images and generated outputs under `data/`.
- Batch/job state is in-memory (restarting the server clears active status history).
- Each image job uses one uploaded image + one prompt and calls:
  - `POST /v1/images/edits`
