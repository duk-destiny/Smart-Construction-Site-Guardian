/** S1082 键盘可达：非交互元素上的 onClick 需要等价键盘入口。
 * 用法：元素加 role="button" tabIndex={0} onKeyDown={activateOnKey}，
 * Enter/Space 触发一次等价 click（阻止空格滚动页面）。 */
export function activateOnKey(e: React.KeyboardEvent) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    ;(e.currentTarget as HTMLElement).click()
  }
}
