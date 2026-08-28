# Hesba Global Asset Pack — Build Checklist

Status: ACTIVE GAP CHECKLIST

## Phase P0 — blocks clean Master Data implementation

### Brand canonicalization
- [ ] Copy/derive canonical full logo from official Ahmed source
- [ ] Copy/derive canonical logo mark
- [ ] Canonical app icon
- [ ] Canonical splash mark
- [ ] Canonical watermark
- [ ] Reusable brand pattern

### Shared icon family
- [ ] Navigation family
- [ ] Master Data family
- [ ] Common action family
- [ ] Header/system family
- [ ] Status family

P0 acceptance:
- one consistent style;
- SVG preferred;
- works on light background;
- works at 16/20/24/32 px;
- no embedded text;
- no random third-party mixed families;
- RTL directional behavior documented;
- approved by Ahmed as one family.

## Phase P1 — operating cycle
- [ ] Purchases icon family
- [ ] Inventory icon family
- [ ] Sales icon family
- [ ] Cashbox/money icon family
- [ ] Reports icon family
- [ ] Communication icon family
- [ ] Alert icon family

P1 acceptance:
Same geometry/weight/style as P0.

## Phase P2 — visual polish
- [ ] Onboarding illustration set
- [ ] Empty-state illustration set
- [ ] Generic reusable brand texture/pattern

## Current repository reuse
Do not recreate these unless there is a specific reason:
- Login backgrounds
- Setup Gate background
- Setup Gate hero
- Setup Gate step icons
- Setup activity icons

They remain screen-specific and should not be reused globally by default.

## Agent behavior while gaps remain
If a Screen Pack requests a missing P0 asset:
- do not substitute an invented icon;
- mark ASSET GAP;
- continue non-dependent planning/implementation;
- use an approved neutral CSS shape only if the Screen Pack explicitly allows it.

## Next Main Control action
Build one P0 visual icon family first and show it as a compact style sheet:
- Brand mark
- 4 navigation icons
- 6 Master Data icons
- 6 action/system icons
- 4 status icons

Once Ahmed approves the family style, complete the rest of P0 in the same style without repeated micro-approval.
