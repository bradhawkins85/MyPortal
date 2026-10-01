(function () {
  function supportsPasskeys() {
    return Boolean(window.PublicKeyCredential && navigator.credentials);
  }

  function decodeBase64Url(value) {
    const padding = '='.repeat((4 - (value.length % 4 || 4)) % 4);
    const base64 = `${value}${padding}`.replace(/-/g, '+').replace(/_/g, '/');
    const raw = window.atob(base64);
    const bytes = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) {
      bytes[index] = raw.charCodeAt(index);
    }
    return bytes.buffer;
  }

  function encodeBase64Url(buffer) {
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    let binary = '';
    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });
    return window.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/u, '');
  }

  function decodeCredentialOptions(publicKey) {
    const options = { ...publicKey };
    if (typeof options.challenge === 'string') {
      options.challenge = decodeBase64Url(options.challenge);
    }
    if (options.user && typeof options.user.id === 'string') {
      options.user = { ...options.user, id: decodeBase64Url(options.user.id) };
    }
    if (Array.isArray(options.excludeCredentials)) {
      options.excludeCredentials = options.excludeCredentials.map((credential) => ({
        ...credential,
        id: decodeBase64Url(credential.id),
      }));
    }
    if (Array.isArray(options.allowCredentials)) {
      options.allowCredentials = options.allowCredentials.map((credential) => ({
        ...credential,
        id: decodeBase64Url(credential.id),
      }));
    }
    return options;
  }

  function serializeCredential(credential) {
    return {
      id: credential.id,
      type: credential.type,
      rawId: encodeBase64Url(credential.rawId),
      authenticatorAttachment: credential.authenticatorAttachment || null,
      response: {
        clientDataJSON: encodeBase64Url(credential.response.clientDataJSON),
        ...(credential.response.attestationObject
          ? {
              attestationObject: encodeBase64Url(credential.response.attestationObject),
              transports:
                typeof credential.response.getTransports === 'function'
                  ? credential.response.getTransports()
                  : [],
            }
          : {}),
        ...(credential.response.authenticatorData
          ? { authenticatorData: encodeBase64Url(credential.response.authenticatorData) }
          : {}),
        ...(credential.response.signature
          ? { signature: encodeBase64Url(credential.response.signature) }
          : {}),
        ...(credential.response.userHandle
          ? { userHandle: encodeBase64Url(credential.response.userHandle) }
          : {}),
      },
    };
  }

  function passkeyErrorMessage(error, fallback) {
    if (!error || !error.name) {
      return fallback;
    }
    if (error.name === 'NotAllowedError') {
      return 'The passkey request was cancelled or timed out. You can still use another sign-in option.';
    }
    if (error.name === 'InvalidStateError') {
      return 'This passkey is already registered to your account.';
    }
    if (error.name === 'NotSupportedError' || error.name === 'SecurityError') {
      return 'Passkeys are not available in this browser or on this connection.';
    }
    return fallback;
  }

  window.MyPortalPasskeyUtils = {
    supportsPasskeys,
    decodeCredentialOptions,
    serializeCredential,
    passkeyErrorMessage,
  };
})();
