// genlayer-js request envelopes carry bigints; teach JSON.stringify to print them.
if (typeof BigInt !== "undefined") {
  const proto = BigInt.prototype as unknown as { toJSON?: () => string };
  if (typeof proto.toJSON !== "function") {
    proto.toJSON = function (this: bigint): string {
      return this.toString();
    };
  }
}
export {};
