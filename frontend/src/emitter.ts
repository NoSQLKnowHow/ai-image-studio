/** A list of listeners and a way to tell them something: for an event that one page of the app hears and another page announces, without either holding the other. */
export interface Emitter<T> {
  /** Listen. Returns the way to stop listening. */
  subscribe: (listener: (value: T) => void) => () => void;
  /** Tell every listener. One that throws does not stop the rest from being told. */
  emit: (value: T) => void;
}

export function createEmitter<T>(): Emitter<T> {
  const listeners = new Set<(value: T) => void>();
  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    emit(value) {
      for (const listener of [...listeners]) {
        try {
          listener(value);
        } catch (error) {
          console.error("an event listener failed", error); // the others are still told
        }
      }
    },
  };
}
