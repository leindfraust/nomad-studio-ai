import "@testing-library/jest-dom/vitest";

/** jsdom has no EventSource. The app only needs construction, message and close. */
class TestEventSource {
  static instances: TestEventSource[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    TestEventSource.instances.push(this);
  }

  emit(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  close(): void {
    this.closed = true;
  }
}

Object.assign(globalThis, { EventSource: TestEventSource });

export { TestEventSource };
