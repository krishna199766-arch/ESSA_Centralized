// Colours and the shared stylesheet. Split out of App.js when the GRN screens
// arrived so both halves of the app — detailing a product and receiving a GRN —
// look like one app rather than two.
import { StyleSheet, Platform } from 'react-native';

// react-native has no cross-platform monospace alias
const MONO = Platform.OS === 'ios' ? 'Menlo' : 'monospace';

export const C = {
  bg: '#0f1420', panel: '#171d2b', panel2: '#1e2636', line: '#2a3346',
  text: '#e8edf5', muted: '#8a95a8', accent: '#4f8cff', ok: '#35c07a', warn: '#f2b544',
  err: '#f2685f',
};

export const s = StyleSheet.create({
  center: { flex: 1, backgroundColor: C.bg, alignItems: 'center', justifyContent: 'center', padding: 24 },
  brand: { color: C.text, fontSize: 24, fontWeight: '800', marginTop: 6 },
  logoAI: { color: C.accent, fontSize: 44, fontWeight: '900', letterSpacing: 2 },
  sub: { color: C.muted, fontSize: 13, marginTop: 4, marginBottom: 18 },
  card: { width: '100%', maxWidth: 420, backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 14, padding: 20 },
  fieldLabel: { color: C.muted, fontSize: 12, marginBottom: 5 },
  input: { backgroundColor: C.panel2, borderColor: C.line, borderWidth: 1, borderRadius: 9, paddingHorizontal: 12, paddingVertical: 11, color: C.text, fontSize: 15 },
  hint: { color: C.muted, fontSize: 11, marginTop: 2, marginBottom: 8 },
  err: { color: C.err, fontSize: 13, marginBottom: 8, textAlign: 'center' },
  btn: { backgroundColor: C.accent, borderRadius: 9, paddingVertical: 13, alignItems: 'center', marginTop: 6 },
  btnText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  btnSm: { backgroundColor: C.accent, borderRadius: 8, paddingHorizontal: 16, justifyContent: 'center' },
  btnSmText: { color: '#fff', fontWeight: '700' },
  link: { color: C.accent, fontSize: 13, textAlign: 'center', marginTop: 12 },
  topbar: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 12, borderBottomColor: C.line, borderBottomWidth: 1, backgroundColor: C.panel },
  topTitle: { color: C.text, fontSize: 18, fontWeight: '700', flex: 1 },
  topCount: { color: C.muted, fontSize: 12 },
  chip: { backgroundColor: C.panel2, borderColor: C.line, borderWidth: 1, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 7 },
  chipOn: { backgroundColor: C.accent, borderColor: C.accent },
  chipText: { color: C.muted, fontSize: 13, textTransform: 'capitalize' },
  prodRow: { flexDirection: 'row', alignItems: 'center', backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 10, padding: 13, marginBottom: 8 },
  prodName: { color: C.text, fontSize: 15, fontWeight: '600' },
  prodMeta: { color: C.muted, fontSize: 12, marginTop: 3 },
  badge: { borderRadius: 20, paddingHorizontal: 10, paddingVertical: 4 },
  badgeDone: { backgroundColor: '#12301f' }, badgePend: { backgroundColor: '#3a2f13' },
  detName: { color: C.text, fontSize: 20, fontWeight: '700' },
  modalWrap: { flex: 1, backgroundColor: 'rgba(0,0,0,.6)', justifyContent: 'flex-end' },
  modalCard: { backgroundColor: C.panel, borderTopLeftRadius: 16, borderTopRightRadius: 16, paddingBottom: 24 },
  modalHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 16, borderBottomColor: C.line, borderBottomWidth: 1 },
  modalTitle: { color: C.text, fontSize: 16, fontWeight: '700' },
  optRow: { paddingHorizontal: 18, paddingVertical: 14, borderBottomColor: C.line, borderBottomWidth: 1 },

  // ---- shared small pieces ----
  mono: { fontFamily: MONO, letterSpacing: 0.2 },
  tabbar: { flexDirection: 'row', backgroundColor: C.panel, borderBottomColor: C.line, borderBottomWidth: 1 },
  tab: { flex: 1, alignItems: 'center', paddingVertical: 12, borderBottomWidth: 2, borderBottomColor: 'transparent' },
  tabOn: { borderBottomColor: C.accent },
  tabText: { color: C.muted, fontSize: 14, fontWeight: '600' },
  tabTextOn: { color: C.text },
  btnGhost: { backgroundColor: C.panel2, borderColor: C.line, borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 9, alignItems: 'center' },
  btnGhostText: { color: C.text, fontSize: 13, fontWeight: '600' },
  actionbar: { borderTopColor: C.line, borderTopWidth: 1, backgroundColor: C.panel, padding: 12, gap: 8 },

  // ---- GRN ----
  grnRow: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 10, padding: 13, marginBottom: 8 },
  lineCard: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 10, padding: 13, marginBottom: 10 },
  lineTitle: { color: C.text, fontSize: 15, fontWeight: '600', flex: 1 },
  varRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9, borderTopColor: C.line, borderTopWidth: 1 },
  varLabel: { color: C.text, fontSize: 13, fontWeight: '600' },
  rowCard: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 10, padding: 12, marginBottom: 10 },
  rowHead: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 10 },
  rowIndex: { color: C.muted, fontSize: 12, fontWeight: '700' },
  bar: { height: 5, borderRadius: 3, backgroundColor: C.panel2, overflow: 'hidden', marginTop: 8 },
  barFill: { height: 5, borderRadius: 3, backgroundColor: C.accent },
  qrBox: { backgroundColor: '#fff', borderRadius: 6, padding: 3 },
  sectionLabel: { color: C.muted, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.6, marginTop: 4, marginBottom: 8 },
});
