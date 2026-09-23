# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


# QboPhysicalAddressSyncRequest DELETED with the /sync route it served (U-519
# fix round 2). Its `address_id` was a caller-controlled LOCAL upsert key over an
# unscoped lookup, not a remote selector; see the note in api/router.py. Its
# `access_token` was separately a required field the service documents as ignored
# ("QboHttpClient resolves and refreshes the token lazily"), i.e. an OpenAPI
# contract instructing callers to put a live OAuth bearer token in a request body
# for no reason — that surface goes away with the model.

# QboPhysicalAddressCreate / QboPhysicalAddressUpdate DELETED with the package's
# entire write surface (U-519 round 3). Both declared a caller-settable `qbo_id`,
# and `UpdateQboPhysicalAddressById` SETs [QboId] under a WHERE with no realm or
# owner predicate -- so the pair was an identity re-stamp primitive, reachable by
# any role with QBO_SYNC can_update (Controller included). See api/router.py.
