# Object Storage Adapters

This directory is the project-specific adapter area for the object storage
package. In this worker project the import root is `app.object_storage`; in a
copied Triton model, keep the same directory name and import root
`object_storage`.

The parent package contains reusable storage primitives. Files in this directory
convert business payload shapes into those primitives. When copying
`object_storage` to another project, this directory can be replaced,
trimmed, or removed if the project does not need adapters.

## Boundary

```text
app/object_storage/
  contract.py      reusable validation
  aliyun_oss.py    reusable provider/client/presigned URL
  refs.py          reusable object/ref builders

app/object_storage/adapters/
  *.py             project-specific payload adapters
```

Adapters should not perform object storage I/O. They should parse, validate, and
convert payload fields.

## Minimal Adapter Shape

See `example.py` for a small copy-and-edit adapter. Keep it as a guide, or
delete it when a project has concrete adapters.

```python
from collections.abc import Mapping
from typing import Any

from object_storage import build_object_ref, build_storage_object  # Replace with this project's import root.


def input_ref_from_business_payload(payload: Mapping[str, Any], presigned_url: str) -> dict[str, Any]:
    size_bytes = payload.get("size_bytes")
    return build_object_ref(
        obj=build_storage_object(
            provider="aliyun_oss",
            bucket=str(payload["bucket"]),
            region=str(payload["region"]),
            key=str(payload["key"]),
            content_type=str(payload["content_type"]),
            size_bytes=int(size_bytes) if size_bytes is not None else None,
            sha256=payload.get("sha256"),
        ),
        presigned_url=presigned_url,
    )
```

## Rules

- Keep common behavior in `contract.py`, `refs.py`, or provider modules.
- Put business field mapping in this directory.
- Import adapters explicitly from their module, for example
  `<import_root>.adapters.oss_url_ref`.
- Do not re-export project adapters from the package `__init__`.
