from .chat import (
    start, reset, help_command, history_command,
    handle_message, handle_voice,
    admin_command, handle_admin_callback,
    block_user_command, unblock_user_command, admin_stats_command,
)
from .photo import photo_command
from .telephony import (
    record_command, vrec_command, crec_command, krec_command,
    stats_command, vstats_command, cstats_command, kstats_command,
    handle_stats_callback,
    debug_pbx_command, debug_voiso_command, debug_vrec_command, debug_coperato_command,
    debug_croco_command,
)
