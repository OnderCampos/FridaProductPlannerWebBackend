import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Read env vars lazily (use get to avoid KeyError at import time)
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
"""The Pinecone API key (may be None until configured in env)."""

PINECONE_ENVIRONMENT = os.environ.get("PINECONE_ENVIRONMENT")
"""The Pinecone environment (legacy client)."""

KNOWLEDGEBASE_INDEX_NAME = os.environ.get("KNOWLEDGEBASE_INDEX_NAME")
EMBEDDING_URL = os.environ.get("EMBEDDING_URL")

# Lazy client holder and compatibility handling for new vs legacy pinecone package
_pinecone_client = None
_pinecone_is_legacy = False

try:
	# New pinecone API (object-oriented)
	from pinecone import Pinecone, ServerlessSpec  # type: ignore
	_HAS_NEW_PINECONE = True
	_legacy = None
except Exception:
	# Fallback to legacy pinecone package which exposed init()
	try:
		import pinecone as _legacy  # type: ignore
		Pinecone = None  # type: ignore
		ServerlessSpec = None  # type: ignore
		_HAS_NEW_PINECONE = False
	except Exception:
		# No pinecone package available; keep flags set and let callers handle
		_legacy = None
		Pinecone = None  # type: ignore
		ServerlessSpec = None  # type: ignore
		_HAS_NEW_PINECONE = False


def get_pinecone_client():
	"""Return a Pinecone client instance.

	This function uses the new Pinecone class when available. If not, it will
	initialize and return the legacy pinecone module. It's intentionally lazy
	so importing this module does not attempt network/credential operations.
	"""
	global _pinecone_client, _pinecone_is_legacy

	if _pinecone_client is not None:
		return _pinecone_client

	if _HAS_NEW_PINECONE and Pinecone is not None:
		if not PINECONE_API_KEY:
			raise RuntimeError("PINECONE_API_KEY is not set for new Pinecone client")
		# Create a new Pinecone client instance
		_pinecone_client = Pinecone(api_key=PINECONE_API_KEY)
		_pinecone_is_legacy = False
		return _pinecone_client

	# Legacy fallback
	if _legacy is None:
		raise RuntimeError("No pinecone client library is available (neither new nor legacy)")
	if not PINECONE_API_KEY or not PINECONE_ENVIRONMENT:
		raise RuntimeError("PINECONE_API_KEY and PINECONE_ENVIRONMENT must be set for legacy pinecone client.")
	# initialize legacy client
	_legacy.init(api_key=PINECONE_API_KEY, environment=PINECONE_ENVIRONMENT)
	_pinecone_client = _legacy
	_pinecone_is_legacy = True
	return _pinecone_client


def ensure_index(name: str, dimension: int, metric: str = "cosine", serverless_spec_kwargs: dict | None = None):
	"""Ensure that an index with `name` exists in Pinecone.

	Works with both the new Pinecone client and the legacy package.
	"""
	pc = get_pinecone_client()

	if _pinecone_is_legacy:
		existing = pc.list_indexes()
		if name not in existing:
			pc.create_index(name=name, dimension=dimension, metric=metric)
		return

	# New client: adapt to potential return shapes
	try:
		indexes = pc.list_indexes()
		try:
			names = indexes.names()
		except Exception:
			names = list(indexes) if indexes is not None else []
		if name not in names:
			spec = None
			if serverless_spec_kwargs and ServerlessSpec is not None:
				spec = ServerlessSpec(**serverless_spec_kwargs)
			if spec is not None:
				pc.create_index(name=name, dimension=dimension, metric=metric, spec=spec)
			else:
				pc.create_index(name=name, dimension=dimension, metric=metric)
	except Exception as e:
		raise RuntimeError(f"Error ensuring index '{name}': {e}") from e



