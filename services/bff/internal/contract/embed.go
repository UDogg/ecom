package contract

import _ "embed"

// Default is the contract compiled into the BFF binary: the deployed API is
// exactly what was reviewed, with nothing to edit on the running host.
//
//go:embed contract.json
var Default []byte
