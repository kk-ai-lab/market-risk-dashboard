import { Buffer } from "node:buffer";
import { webcrypto } from "node:crypto";

function copyBufferSource(data: BufferSource): Buffer {
  const bytes = ArrayBuffer.isView(data)
    ? new Uint8Array(data.buffer as ArrayBuffer, data.byteOffset, data.byteLength)
    : new Uint8Array(data as ArrayBuffer);
  return Buffer.from(bytes);
}

export function nodeWebCrypto(): Crypto {
  return {
    subtle: {
      digest: (algorithm: AlgorithmIdentifier, data: BufferSource) =>
        webcrypto.subtle.digest(algorithm, copyBufferSource(data)),
    },
  } as unknown as Crypto;
}
