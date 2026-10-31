# External email identity

External email is third-party content. It must not be presented as authored or
authorized by the human account that owns its WebConnect route.

The connector preserves the provider-supplied sender claim in
`metadata.external_email.from`. `from_display` and `signature` identify that
sender and explicitly label the message as external email. The legacy `from`
and `originator` fields remain the internal return route for compatibility;
`routing_owner` makes this distinction explicit. They are not proof of human
authorship or permission. Email sender claims are not proof of identity either.

The external metadata records `approval_required: true`. New message bodies
also explain the boundary: ordinary conversation and informational replies can
proceed within existing authorization, but changes and consequential actions
require owner approval. This is agent guidance, not an OS sandbox or a mechanism
for automatically determining whether an action has been authorized.

Replies retain their original reference so the connector can recover the
external thread ID and the relay can check account/node ownership before email
delivery. Mailbox profiles and grants are not changed by external messages.

The local viewer and WebConnect distinguish external authors from routing
owners. Reload browser pages after updating their JavaScript. Restart the
connector for ingestion changes; supervisor prompt changes take effect on its
next restart. Existing conversation notifications cannot be rewritten by a
mailbox metadata correction.
