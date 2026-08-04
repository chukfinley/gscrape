"""The `pb=` protobuf-over-URL parameters Maps endpoints take.

Google encodes request protos into the URL as `!<field><type><value>` runs:
`!1m14` opens a message in field 1 with 14 following elements, `!1s<str>` is a
string, `!2b1` a bool, `!3d1.5` a double, `!1e2` an enum. Nothing here was
guessed — the templates below are live requests captured from the Maps web app
with the varying parts (feature id, viewport, session) parameterised.

Editing these is the single most dangerous thing in the package: the toggle
blocks decide which sections Google bothers to include, and a malformed run
silently yields a payload that parses fine but is missing photos or hours. When
Maps changes, re-capture instead of hand-editing — open the Maps web app with
devtools, copy the `preview/place` request, diff it against the template.
"""

from __future__ import annotations

# `/maps/preview/place`. {fid} is the `0x<hex>:0x<hex>` feature id, {lat}/{lng}
# the viewport centre, {session} the ui session id (`_` works fine).
#
# The interesting blocks:
#   13m57  photo carousel + photo categories + dish photos + top reviews
#   15m8   review section shape
#   72m22  the UGC post types to embed (top reviews live here)
#   34m5   business status / edit affordances
PLACE = (
    "!1m14!1s{fid}"
    "!3m12!1m3!1d149622.85!2d{lng}!3d{lat}!2m3!1f0.0!2f0.0!3f0.0!3m2!1i1024!2i768!4f13.1"
    "!12m4!2m3!1i360!2i120!4i8"
    "!13m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
    "!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3"
    "!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
    "!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
    "!14m3!1s{session}!7e81!15i10112"
    "!15m108!1m26!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1"
    "!18m15!3b1!4b1!5b1!6b1!13b1!14b1!17b1!21b1!22b1!30b1!32b1!33m1!1b1!34b1!36e2"
    "!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1"
    "!36b1!37b1!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1"
    "!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298"
    "!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1"
    "!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4"
    "!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts"
    "!6m1!1e1!9b1!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1"
    "!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1!127b1!128m1!1b0"
    "!21m0!22m1!1e81!30m8!3b1!6m2!1b1!2b1!7m2!1e3!2b1!9b1"
    "!34m5!7b1!10b1!14b1!15m1!1b0!37i787"
)


def place(fid: str, lat: float = 0.0, lng: float = 0.0, session: str = "_") -> str:
    return PLACE.format(fid=fid, lat=lat or 0.0, lng=lng or 0.0, session=session)
