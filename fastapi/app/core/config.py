import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DOTENV_PATH = BASE_DIR / '.env'
if DOTENV_PATH.exists():
    load_dotenv(DOTENV_PATH)

CONFIG_PATH = BASE_DIR / "config.yml"


def resolve_path(value: str) -> str:
    """Absolute path, resolving relative values against the backend directory
    so the app behaves the same whatever directory it is started from."""
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (BASE_DIR / path))

def load_config():
    cfg = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            cfg = yaml.safe_load(f) or {}
    # Overlay envs
    cfg['telnyx'] = cfg.get('telnyx', {})
    cfg['telnyx']['mode'] = os.getenv('SMS_MODE', cfg['telnyx'].get('mode', 'simulation'))
    cfg['telnyx']['api_key'] = os.getenv('TELNYX_API_KEY', cfg['telnyx'].get('api_key'))
    cfg['telnyx']['from_number'] = os.getenv('TELNYX_FROM_NUMBER', cfg['telnyx'].get('from_number', '+12245798015'))
    # Ed25519 public key from the Telnyx portal; when set, inbound webhooks must
    # carry a valid signature.
    cfg['telnyx']['public_key'] = os.getenv('TELNYX_PUBLIC_KEY', cfg['telnyx'].get('public_key', '')) or ''
    # Empty means "simulate in-process" (no external simulator port required).
    # Set SIMULATION_SMS_URL to delegate to Simulation/outbound.py instead.
    cfg['telnyx']['simulation_url'] = os.getenv(
        'SIMULATION_SMS_URL', cfg['telnyx'].get('simulation_url', '') or '')

    postgres_url = os.getenv('POSTGRES_URL')
    if postgres_url:
        database_url = postgres_url
    else:
        sqlite_path = BASE_DIR / 'listingiq.db'
        database_url = f"sqlite:///{sqlite_path}"

    cfg['database'] = {
        'url': database_url
    }
    cfg['google'] = {
        'client_id': os.getenv('GOOGLE_CLIENT_ID', cfg.get('google', {}).get('client_id')),
        'client_secret': os.getenv('GOOGLE_CLIENT_SECRET', cfg.get('google', {}).get('client_secret')),
        'redirect_uri': os.getenv('GOOGLE_OAUTH_REDIRECT_URI', cfg.get('google', {}).get('redirect_uri'))
    }
    # Bobbie / DeepSeek conversational AI.
    ai_cfg = cfg.get('ai', {}) if isinstance(cfg.get('ai', {}), dict) else {}
    cfg['ai'] = {
        'api_key': os.getenv('DEEPSEEK_API_KEY') or os.getenv('AI_API_KEY') or ai_cfg.get('api_key'),
        'base_url': (os.getenv('AI_BASE_URL') or ai_cfg.get('base_url') or 'https://api.deepseek.com').rstrip('/'),
        'model': os.getenv('AI_MODEL') or ai_cfg.get('model') or 'deepseek-v4-flash',
        'timeout_seconds': float(os.getenv('AI_REQUEST_TIMEOUT_MS', ai_cfg.get('timeout_ms', 30000))) / 1000.0,
        'timezone': os.getenv('BOBBIE_TIMEZONE', ai_cfg.get('timezone', 'America/Chicago')),
        # Retrieval embeddings: MiniLM ONNX bundled with chromadb (local, free).
        'embedding_model': os.getenv('EMBEDDING_MODEL', ai_cfg.get('embedding_model', 'sentence-transformers/all-MiniLM-L6-v2')),
    }
    # Services the SMS workspace talks to (all reused from the Simulation folder).
    sms_cfg = cfg.get('sms', {}) if isinstance(cfg.get('sms', {}), dict) else {}
    # Bobbie's knowledge index and the meeting calendar run inside this app.
    # Both URLs are opt-in overrides that delegate to the standalone Simulation
    # services instead; empty (the default) keeps everything on port 8000.
    cfg['sms'] = {
        'rag_url': os.getenv('BOBBIE_RAG_URL', sms_cfg.get('rag_url', '') or ''),
        'calendar_url': os.getenv('CALENDAR_API_URL', sms_cfg.get('calendar_url', '') or '').rstrip('/'),
        'knowledge_pdf': resolve_path(
            os.getenv('BOBBIE_KNOWLEDGE_PDF')
            or sms_cfg.get('knowledge_pdf')
            or 'data/knowledge/Bobbie_Fisher_REMAX_Comprehensive_Profile_and_AI_Knowledge_Base.pdf'
        ),
        'knowledge_collection': os.getenv('KNOWLEDGE_COLLECTION',
                                          sms_cfg.get('knowledge_collection', 'bobbie_knowledge')),
        'knowledge_chunk_chars': int(os.getenv('KNOWLEDGE_CHUNK_CHARS',
                                               sms_cfg.get('knowledge_chunk_chars', 900))),
        'knowledge_chunk_overlap': int(os.getenv('KNOWLEDGE_CHUNK_OVERLAP',
                                                 sms_cfg.get('knowledge_chunk_overlap', 150))),
        'calendar_days': int(os.getenv('CALENDAR_DAYS', sms_cfg.get('calendar_days', 14))),
        'calendar_slot_minutes': int(os.getenv('CALENDAR_SLOT_MINUTES', sms_cfg.get('calendar_slot_minutes', 30))),
        'reply_delay_seconds': float(os.getenv('AI_REPLY_DELAY_SECONDS', sms_cfg.get('reply_delay_seconds', 1.4))),
        'queue_cooldown_seconds': float(os.getenv('LEAD_QUEUE_COOLDOWN_MS', sms_cfg.get('queue_cooldown_ms', 5000))) / 1000.0,
        'lead_job_timeout_seconds': float(os.getenv('LEAD_JOB_TIMEOUT_MS', sms_cfg.get('lead_job_timeout_ms', 0))) / 1000.0,
        'followup_enabled': str(os.getenv('BOBBIE_FOLLOWUP_ENABLED', sms_cfg.get('followup_enabled', 'true'))).lower() in ('1', 'true', 'yes', 'on'),
        'followup_first_delay_hours': float(os.getenv('BOBBIE_FOLLOWUP_FIRST_HOURS', sms_cfg.get('followup_first_delay_hours', 48))),
        'followup_gap_hours': float(os.getenv('BOBBIE_FOLLOWUP_GAP_HOURS', sms_cfg.get('followup_gap_hours', 72))),
        'followup_max_attempts': int(os.getenv('BOBBIE_FOLLOWUP_MAX_ATTEMPTS', sms_cfg.get('followup_max_attempts', 3))),
        'followup_poll_seconds': float(os.getenv('BOBBIE_FOLLOWUP_POLL_SECONDS', sms_cfg.get('followup_poll_seconds', 60))),
    }
    # ChromaDB lives inside the project and remains the default. Qdrant is
    # selected only when both server-side credentials are present and valid.
    # CHROMA_PATH is the current name; LANCEDB_PATH is still read so
    # existing .env files keep working.
    vector_cfg = cfg.get('vector_store', {}) if isinstance(cfg.get('vector_store', {}), dict) else {}
    cfg['vector_store'] = {
        'path': resolve_path(
            os.getenv('CHROMA_PATH')
            or os.getenv('LANCEDB_PATH')
            or vector_cfg.get('path')
            or 'data/chroma'
        ),
        'qdrant_url': os.getenv('QDRANT_URL', '').strip(),
        'qdrant_api_key': os.getenv('QDRANT_API_KEY', '').strip(),
        'qdrant_timeout_seconds': float(os.getenv('QDRANT_TIMEOUT_SECONDS', 10)),
        'qdrant_cloud_inference': str(os.getenv(
            'QDRANT_CLOUD_INFERENCE', 'true')).lower() in ('1', 'true', 'yes', 'on'),
        'embedding_model': cfg['ai']['embedding_model'],
        'vector_dimension': int(os.getenv('VECTOR_DIMENSION', 384)),
    }
    # Lead pool CSV/XLSX uploads. Files are streamed to disk, so the ceiling
    # bounds disk and parse time rather than memory.
    leads_cfg = cfg.get('leads', {}) if isinstance(cfg.get('leads', {}), dict) else {}
    cfg['leads'] = {
        'upload_max_mb': int(os.getenv('LEAD_UPLOAD_MAX_MB', leads_cfg.get('upload_max_mb', 50))),
        'staging_dir': resolve_path(
            os.getenv('LEAD_UPLOAD_DIR') or leads_cfg.get('staging_dir') or 'data/uploads'),
        # How long a previewed file waits on disk for its import to be confirmed.
        'staging_ttl_minutes': int(os.getenv('LEAD_UPLOAD_TTL_MINUTES',
                                             leads_cfg.get('staging_ttl_minutes', 30))),
    }
    geocoding_cfg = cfg.get('geocoding', {}) if isinstance(cfg.get('geocoding', {}), dict) else {}
    cfg['geocoding'] = {
        'provider': os.getenv('GEOCODING_PROVIDER', geocoding_cfg.get('provider', 'nominatim')),
        'nominatim_base_url': (
            os.getenv('NOMINATIM_BASE_URL')
            or geocoding_cfg.get('nominatim_base_url')
            or 'https://nominatim.openstreetmap.org'
        ).rstrip('/'),
        'country': os.getenv('GEOCODING_COUNTRY', geocoding_cfg.get('country', 'us')).lower(),
        'request_interval_seconds': float(os.getenv(
            'GEOCODING_REQUEST_INTERVAL_SECONDS',
            geocoding_cfg.get('request_interval_seconds', 1.1),
        )),
        'timeout_seconds': float(os.getenv(
            'GEOCODING_TIMEOUT_SECONDS', geocoding_cfg.get('timeout_seconds', 12),
        )),
        'user_agent': os.getenv(
            'NOMINATIM_USER_AGENT',
            geocoding_cfg.get('user_agent', 'ListingIQ/1.0 (lead-geocoding)'),
        ),
        'max_retries': int(os.getenv(
            'GEOCODING_MAX_RETRIES', geocoding_cfg.get('max_retries', 3),
        )),
    }
    cfg['redis_url'] = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379')
    cfg['frontend_url'] = os.getenv('FRONTEND_URL', cfg.get('frontend_url', 'http://localhost:3000'))
    smtp_port = os.getenv('SMTP_PORT', cfg.get('smtp', {}).get('port') if isinstance(cfg.get('smtp', {}), dict) else None)
    if smtp_port is None or smtp_port == '':
        smtp_port = 587
    smtp_user = os.getenv('SMTP_USER', cfg.get('smtp', {}).get('user'))
    cfg['smtp'] = {
        'host': os.getenv('SMTP_HOST', cfg.get('smtp', {}).get('host')),
        'port': int(smtp_port),
        'user': smtp_user,
        'password': os.getenv('SMTP_PASSWORD', cfg.get('smtp', {}).get('password')),
        # Fall back to the authenticated mailbox so a minimal local setup works.
        'from_address': os.getenv('SMTP_FROM', cfg.get('smtp', {}).get('from')) or smtp_user,
        # Amazon SES uses STARTTLS on 587 and implicit TLS on 465.
        'use_ssl': os.getenv('SMTP_SSL', str(cfg.get('smtp', {}).get('ssl', ''))).lower() in ('1', 'true', 'yes'),
    }
    return cfg

settings = load_config()
