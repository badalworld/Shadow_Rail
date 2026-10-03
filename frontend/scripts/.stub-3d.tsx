import React from 'react'
function Stub(props: any) { return React.createElement('div', { 'data-stub': '3d' }, props?.children ?? null) }
export default Stub
export const BotMap3D = Stub
export const Canvas = Stub
export const Html = Stub
export const Sparkles = Stub
export const ContactShadows = Stub
export const Environment = Stub
export const Lightformer = Stub
export const OrbitControls = Stub
export const MeshReflectorMaterial = Stub
export const SoftShadows = Stub
export const Stage = Stub
export const Text = Stub
export const Billboard = Stub
export const RoundedBox = Stub
export function useFrame() {}
export function useThree() { return {} }
export function useCursor() {}
export function useGLTF() { return { scene: null } }
export function resolveNode() { return [0, 0, 0] }
export function useNodePositions() { return [] }
