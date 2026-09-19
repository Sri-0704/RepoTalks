import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import settings
from backend.services.gemini_service import gemini_service
from backend.services.vector_store import vector_store
from backend.services.ingestion_service import ingestion_service
from backend.services.viva_service import viva_service
from backend.services.tracer_service import tracer_service
from backend.services.architecture_service import architecture_service
from backend.services.audience_service import audience_service
from backend.main import app

print("Backend imports and FastAPI app initialized successfully!")
