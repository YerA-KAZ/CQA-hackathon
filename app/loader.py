import os

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.schemas import Conversation


class DatasetLoadError(RuntimeError):
    pass


class Dataset(BaseModel):
    dataset_group_id: str
    total_records: int = Field(ge=0)
    records: list[Conversation]


class DatasetResponse(BaseModel):
    isSuccess: bool
    data: Dataset | None = None


async def load_dataset(
    client: httpx.AsyncClient, group_id: str, *, api_url: str | None = None
) -> Dataset:
    """Load a dataset using the server's configured signed URL."""
    api_url = api_url or os.environ.get("CQA_API_URL")
    if not api_url:
        raise DatasetLoadError("CQA_API_URL is not configured")
    if not group_id.strip():
        raise DatasetLoadError("Dataset group must be specified")

    try:
        url = httpx.URL(api_url).copy_set_param("group_id", group_id)
        response = await client.get(url)
        response.raise_for_status()
        payload = DatasetResponse.model_validate_json(response.content)
    except httpx.HTTPStatusError as error:
        raise DatasetLoadError(f"Dataset API returned HTTP {error.response.status_code}") from None
    except (httpx.RequestError, httpx.InvalidURL):
        raise DatasetLoadError("Could not connect to the dataset API") from None
    except ValidationError:
        raise DatasetLoadError("Dataset API returned an invalid response") from None

    if not payload.isSuccess or payload.data is None:
        raise DatasetLoadError("Dataset API reported a failure")
    if payload.data.dataset_group_id != group_id:
        raise DatasetLoadError("Dataset API returned a different group")
    return payload.data
