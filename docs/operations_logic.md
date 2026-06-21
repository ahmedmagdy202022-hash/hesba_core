# Operations Logic

Operational screens must call controlled backend functions.

## Purchase operation

Creates:

- purchase header
- purchase lines
- stock movement in
- cashbox movement for paid amount
- supplier remaining amount
- audit record

## Sales operation

Creates:

- sales header
- sales lines
- stock movement out
- cashbox movement for paid amount
- customer remaining amount
- profit fields for permitted roles
- audit record

## Payment operation

Customer receipt affects customer and cashbox only.

Supplier payment affects supplier and cashbox only.

## Rule

One operation must be saved as one complete unit.
