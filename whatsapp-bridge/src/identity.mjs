import { jidNormalizedUser } from 'baileys'

export function assertTrustedAccount(user, trustedDigits) {
  const phoneJids = [user?.id, user?.phoneNumber]
    .filter(Boolean)
    .map(jidNormalizedUser)
    .filter(jid => jid.endsWith('@s.whatsapp.net'))
  if (
    !phoneJids.length
    || !phoneJids.includes(`${trustedDigits}@s.whatsapp.net`)
  ) {
    throw new Error(
      'paired WhatsApp account does not match the configured trusted number',
    )
  }
}

export function ownJids(user, trustedDigits) {
  return new Set(
    [
      user?.id,
      user?.lid,
      user?.phoneNumber,
      `${trustedDigits}@s.whatsapp.net`,
    ]
      .filter(Boolean)
      .map(jidNormalizedUser),
  )
}
