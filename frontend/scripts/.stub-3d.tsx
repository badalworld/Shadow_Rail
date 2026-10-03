import React from 'react'
function Stub(props: any) { return React.createElement('div', { 'data-stub': 'bot-map' }, props?.children ?? null) }
export default Stub
export const BotMap3D = Stub
export function resolveNode() { return [0, 0, 0] }
export function useNodePositions() { return [] }
