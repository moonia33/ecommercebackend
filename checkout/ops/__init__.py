from .shared import *  # noqa: F401,F403
from .shared import router  # noqa: F401

# Import endpoint modules for router registration side-effects.
from . import catalog  # noqa: F401
from . import customers  # noqa: F401
from . import inventory  # noqa: F401
from . import invoices  # noqa: F401
from . import jobs  # noqa: F401
from . import orders  # noqa: F401
from . import payments  # noqa: F401
from . import shipments  # noqa: F401

# Keep underscore helpers importable via `from checkout.ops import *`
# (e.g. checkout.ops_api shim used by checkout/tasks.py).
__all__ = [n for n in dir() if not n.startswith("__")]
