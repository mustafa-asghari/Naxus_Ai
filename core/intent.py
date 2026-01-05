from __future__ import annotations

from enum import Enum



class Intent(Enum):
    UNKNOWN = "UNKNOWN"

    # System actions I actually handle today
    OPEN_APP = "OPEN_APP"
    CLOSE_APP = "CLOSE_APP"
    CLOSE_ALL_APPS = "CLOSE_ALL_APPS"
    SEARCH_WEB = "SEARCH_WEB"
    # Future ideas I’m parking here for later
    QUERY_ACTIVITY = "QUERY_ACTIVITY"
    CREATE_NOTE = "CREATE_NOTE"
