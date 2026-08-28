import '@testing-library/jest-dom'

// jsdom 缺 window.matchMedia（antd Grid/响应式组件依赖）→ 最小桩实现
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

// Node 25+ 自带实验性 localStorage：未提供 --localstorage-file 时方法不可用，
// 且会压过 jsdom 的实现 → 统一垫一层内存实现，保证各 Node 版本行为一致。
if (typeof localStorage?.getItem !== 'function') {
  const store = new Map<string, string>()
  const shim = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => { store.set(k, String(v)) },
    removeItem: (k: string) => { store.delete(k) },
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() { return store.size },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: shim, configurable: true,
  })
}
