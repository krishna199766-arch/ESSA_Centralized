import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, ScrollView, FlatList,
  Modal, ActivityIndicator, StyleSheet, KeyboardAvoidingView, Platform, Alert,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import AsyncStorage from '@react-native-async-storage/async-storage';

// ---------------- theme ----------------
const C = {
  bg: '#0f1420', panel: '#171d2b', panel2: '#1e2636', line: '#2a3346',
  text: '#e8edf5', muted: '#8a95a8', accent: '#4f8cff', ok: '#35c07a', warn: '#f2b544',
};

// ---------------- tiny API layer (server + token come from state) ----------------
function makeApi(server, token) {
  const base = (server || '').replace(/\/+$/, '');
  const hdr = { 'Content-Type': 'application/json' };
  return {
    ping: () => fetch(base + '/api/status').then((r) => r.json()),
    login: (username, password) =>
      fetch(base + '/api/auth/login', { method: 'POST', headers: hdr, body: JSON.stringify({ username, password }) })
        .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || 'Login failed'); return j; }),
    verify: () => fetch(base + '/api/auth/verify?token=' + encodeURIComponent(token || '')).then((r) => r.json()),
    options: () => fetch(base + '/api/inventory/product-options').then((r) => r.json()),
    products: (status, q) => fetch(base + `/api/inventory/products?status=${status}&q=${encodeURIComponent(q || '')}`).then((r) => r.json()),
    detail: (id, body) =>
      fetch(base + `/api/inventory/products/${id}/detail`, { method: 'POST', headers: hdr, body: JSON.stringify(body) })
        .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || 'Save failed'); return j; }),
    summary: () => fetch(base + '/api/inventory/summary').then((r) => r.json()),
  };
}

// ---------------- dropdown-with-custom select ----------------
function Select({ label, value, options, onChange }) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState('');
  return (
    <View style={{ marginBottom: 12 }}>
      <Text style={s.fieldLabel}>{label}</Text>
      <TouchableOpacity style={s.input} onPress={() => setOpen(true)}>
        <Text style={{ color: value ? C.text : C.muted }}>{value || 'Select…'}</Text>
      </TouchableOpacity>
      <Modal visible={open} transparent animationType="slide" onRequestClose={() => setOpen(false)}>
        <View style={s.modalWrap}>
          <View style={s.modalCard}>
            <View style={s.modalHead}>
              <Text style={s.modalTitle}>{label}</Text>
              <TouchableOpacity onPress={() => setOpen(false)}><Text style={{ color: C.muted, fontSize: 20 }}>×</Text></TouchableOpacity>
            </View>
            <View style={{ flexDirection: 'row', padding: 10, gap: 8 }}>
              <TextInput style={[s.input, { flex: 1 }]} placeholder="Type a custom value…" placeholderTextColor={C.muted}
                value={custom} onChangeText={setCustom} />
              <TouchableOpacity style={s.btnSm} onPress={() => { if (custom.trim()) { onChange(custom.trim()); setCustom(''); setOpen(false); } }}>
                <Text style={s.btnSmText}>Use</Text></TouchableOpacity>
            </View>
            <FlatList data={options} keyExtractor={(x, i) => x + i} style={{ maxHeight: 360 }}
              renderItem={({ item }) => (
                <TouchableOpacity style={s.optRow} onPress={() => { onChange(item); setOpen(false); }}>
                  <Text style={{ color: item === value ? C.accent : C.text }}>{item}</Text>
                </TouchableOpacity>
              )} />
          </View>
        </View>
      </Modal>
    </View>
  );
}

function Labeled({ label, children }) {
  return <View style={{ marginBottom: 12 }}><Text style={s.fieldLabel}>{label}</Text>{children}</View>;
}

// ---------------- screens ----------------
function ServerScreen({ initial, onSave }) {
  const [url, setUrl] = useState(initial || 'http://192.168.1.5:8000');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const save = async () => {
    setErr(''); setBusy(true);
    try {
      const api = makeApi(url.trim());
      await api.ping();               // reachability check
      await onSave(url.trim());
    } catch (e) { setErr('Could not reach that server. Check the IP, port and same-WiFi.'); }
    setBusy(false);
  };
  return (
    <View style={s.center}>
      <Text style={s.brand}>ESSA <Text style={{ color: C.accent }}>·</Text> Warehouse</Text>
      <Text style={s.sub}>Connect to your ESSA server</Text>
      <View style={s.card}>
        <Labeled label="Server address (from run.sh / run.bat machine)">
          <TextInput style={s.input} autoCapitalize="none" autoCorrect={false} keyboardType="url"
            value={url} onChangeText={setUrl} placeholder="http://192.168.1.5:8000" placeholderTextColor={C.muted} />
        </Labeled>
        <Text style={s.hint}>Find it on that computer: the IP shown by ipconfig/ifconfig, port 8000. Phone must be on the same WiFi.</Text>
        {err ? <Text style={s.err}>{err}</Text> : null}
        <TouchableOpacity style={s.btn} onPress={save} disabled={busy}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={s.btnText}>Connect</Text>}
        </TouchableOpacity>
      </View>
    </View>
  );
}

function LoginScreen({ api, onLogin, onChangeServer }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const submit = async () => {
    setErr(''); setBusy(true);
    try { const r = await api.login(username.trim(), password); onLogin(r.token, r.user); }
    catch (e) { setErr(e.message); setBusy(false); }
  };
  return (
    <View style={s.center}>
      <Text style={s.logoAI}>AI</Text>
      <Text style={s.brand}>ESSA <Text style={{ color: C.accent }}>·</Text> Warehouse</Text>
      <Text style={s.sub}>Sign in to detail products</Text>
      <View style={s.card}>
        <Labeled label="Username"><TextInput style={s.input} autoCapitalize="none" value={username} onChangeText={setUsername} /></Labeled>
        <Labeled label="Password"><TextInput style={s.input} secureTextEntry value={password} onChangeText={setPassword} placeholder="••••••••" placeholderTextColor={C.muted} /></Labeled>
        {err ? <Text style={s.err}>{err}</Text> : null}
        <TouchableOpacity style={s.btn} onPress={submit} disabled={busy}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={s.btnText}>Sign in</Text>}
        </TouchableOpacity>
        <TouchableOpacity onPress={onChangeServer}><Text style={s.link}>Change server</Text></TouchableOpacity>
      </View>
    </View>
  );
}

function ListScreen({ api, onPick, onLogout }) {
  const [items, setItems] = useState([]);
  const [q, setQ] = useState('');
  const [barcode, setBarcode] = useState('');
  const [status, setStatus] = useState('pending');
  const [loading, setLoading] = useState(false);
  const [counts, setCounts] = useState(null);
  const load = useCallback(async (query) => {
    setLoading(true);
    try {
      const data = await api.products(status, query != null ? query : q);
      setItems(data);
      setCounts(await api.summary());
    } catch (e) { Alert.alert('Error', 'Could not load products.'); }
    setLoading(false);
  }, [api, status, q]);
  useEffect(() => { load(); }, [status]); // eslint-disable-line
  const findBarcode = async () => {
    if (!barcode.trim()) return;
    const data = await api.products('all', barcode.trim());
    const exact = data.find((p) => (p.barcode || '') === barcode.trim()) || data[0];
    if (exact) { setBarcode(''); onPick(exact); }
    else Alert.alert('Not found', 'No product with that barcode.');
  };
  return (
    <View style={{ flex: 1 }}>
      <View style={s.topbar}>
        <Text style={s.topTitle}>Products</Text>
        {counts && <Text style={s.topCount}>{counts.pending_detail} pending · {counts.detailed} done</Text>}
        <TouchableOpacity onPress={onLogout}><Text style={s.link}>Logout</Text></TouchableOpacity>
      </View>
      <View style={{ padding: 12, gap: 8 }}>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TextInput style={[s.input, { flex: 1 }]} placeholder="Enter / scan barcode…" placeholderTextColor={C.muted}
            autoCapitalize="characters" value={barcode} onChangeText={setBarcode} onSubmitEditing={findBarcode} returnKeyType="go" />
          <TouchableOpacity style={s.btnSm} onPress={findBarcode}><Text style={s.btnSmText}>Go</Text></TouchableOpacity>
        </View>
        <TextInput style={s.input} placeholder="Search SKU, description…" placeholderTextColor={C.muted}
          value={q} onChangeText={setQ} onSubmitEditing={() => load()} returnKeyType="search" />
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {['pending', 'all', 'detailed'].map((st) => (
            <TouchableOpacity key={st} style={[s.chip, status === st && s.chipOn]} onPress={() => setStatus(st)}>
              <Text style={[s.chipText, status === st && { color: '#fff' }]}>{st}</Text>
            </TouchableOpacity>
          ))}
          <TouchableOpacity style={[s.chip, { marginLeft: 'auto' }]} onPress={() => load()}>
            <Text style={s.chipText}>↻</Text></TouchableOpacity>
        </View>
      </View>
      {loading ? <ActivityIndicator color={C.accent} style={{ marginTop: 20 }} /> : (
        <FlatList data={items} keyExtractor={(x) => String(x.id)} contentContainerStyle={{ padding: 12, paddingTop: 0 }}
          renderItem={({ item }) => (
            <TouchableOpacity style={s.prodRow} onPress={() => onPick(item)}>
              <View style={{ flex: 1 }}>
                <Text style={s.prodName}>{item.description}</Text>
                <Text style={s.prodMeta}>{item.sku}{item.barcode ? ' · ' + item.barcode : ''} · stock {item.stock_qty} {item.uom}</Text>
              </View>
              <View style={[s.badge, item.detailed ? s.badgeDone : s.badgePend]}>
                <Text style={{ color: item.detailed ? C.ok : C.warn, fontSize: 11 }}>{item.detailed ? 'detailed' : 'pending'}</Text>
              </View>
            </TouchableOpacity>
          )}
          ListEmptyComponent={<Text style={{ color: C.muted, textAlign: 'center', marginTop: 30 }}>No products.</Text>} />
      )}
    </View>
  );
}

function DetailScreen({ api, product, options, onBack, onSaved, employee }) {
  const [f, setF] = useState({
    color: product.color || '', size: product.size || '', pattern: product.pattern || '',
    fit: product.fit || '', product_type: product.product_type || '', material: product.material || '',
    design_no: product.design_no || '', mrp: product.mrp != null ? String(product.mrp) : '',
    sale_price: product.sale_price != null ? String(product.sale_price) : '',
    margin_pct: product.margin_pct != null ? String(product.margin_pct) : '',
  });
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((o) => ({ ...o, [k]: v }));
  const num = (v) => (v === '' ? null : Number(v));
  const save = async () => {
    setBusy(true);
    try {
      await api.detail(product.id, {
        ...f, mrp: num(f.mrp), sale_price: num(f.sale_price), margin_pct: num(f.margin_pct),
        detailed_by: employee || 'mobile',
      });
      onSaved();
    } catch (e) { Alert.alert('Save failed', e.message); setBusy(false); }
  };
  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={s.topbar}>
        <TouchableOpacity onPress={onBack}><Text style={s.link}>‹ Back</Text></TouchableOpacity>
        <Text style={s.topTitle} numberOfLines={1}> </Text>
      </View>
      <ScrollView contentContainerStyle={{ padding: 16 }}>
        <Text style={s.detName}>{product.description}</Text>
        <Text style={s.prodMeta}>{product.sku}{product.barcode ? ' · ' + product.barcode : ''} · HSN {product.hsn || '—'} · stock {product.stock_qty} {product.uom}</Text>
        <Text style={s.prodMeta}>from {product.supplier_name || '—'} · cost ₹{product.avg_cost}</Text>
        <View style={{ height: 14 }} />

        <Select label="Color" value={f.color} options={options.color || []} onChange={(v) => set('color', v)} />
        <Select label="Size" value={f.size} options={options.size || []} onChange={(v) => set('size', v)} />
        <Select label="Pattern" value={f.pattern} options={options.pattern || []} onChange={(v) => set('pattern', v)} />
        <Select label="Fit" value={f.fit} options={options.fit || []} onChange={(v) => set('fit', v)} />
        <Select label="Type" value={f.product_type} options={options.product_type || []} onChange={(v) => set('product_type', v)} />
        <Select label="Material" value={f.material} options={options.material || []} onChange={(v) => set('material', v)} />
        <Labeled label="Design No"><TextInput style={s.input} value={f.design_no} onChangeText={(v) => set('design_no', v)} placeholder="e.g. SH-05" placeholderTextColor={C.muted} /></Labeled>
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <View style={{ flex: 1 }}><Labeled label="MRP"><TextInput style={s.input} keyboardType="numeric" value={f.mrp} onChangeText={(v) => set('mrp', v)} placeholder="0" placeholderTextColor={C.muted} /></Labeled></View>
          <View style={{ flex: 1 }}><Labeled label="Sale price"><TextInput style={s.input} keyboardType="numeric" value={f.sale_price} onChangeText={(v) => set('sale_price', v)} placeholder="0" placeholderTextColor={C.muted} /></Labeled></View>
          <View style={{ flex: 1 }}><Labeled label="Margin %"><TextInput style={s.input} keyboardType="numeric" value={f.margin_pct} onChangeText={(v) => set('margin_pct', v)} placeholder="0" placeholderTextColor={C.muted} /></Labeled></View>
        </View>
        {product.detailed && <Text style={[s.hint, { color: C.ok }]}>Already detailed by {product.detailed_by || '—'}. Saving updates it.</Text>}
        <TouchableOpacity style={[s.btn, { marginTop: 8 }]} onPress={save} disabled={busy}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={s.btnText}>Save details</Text>}
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

// ---------------- root ----------------
export default function App() {
  const [ready, setReady] = useState(false);
  const [server, setServer] = useState('');
  const [token, setToken] = useState('');
  const [user, setUser] = useState('');
  const [screen, setScreen] = useState('server');   // server | login | list | detail
  const [options, setOptions] = useState({});
  const [selected, setSelected] = useState(null);

  const api = makeApi(server, token);

  useEffect(() => {
    (async () => {
      const srv = await AsyncStorage.getItem('essa_server');
      const tok = await AsyncStorage.getItem('essa_token');
      const usr = await AsyncStorage.getItem('essa_user');
      if (srv) setServer(srv);
      if (tok) setToken(tok);
      if (usr) setUser(usr);
      if (!srv) setScreen('server');
      else if (!tok) setScreen('login');
      else {
        try {
          const r = await makeApi(srv, tok).verify();
          if (r.ok) { setScreen('list'); loadOptions(srv); }
          else setScreen('login');
        } catch { setScreen('login'); }
      }
      setReady(true);
    })();
  }, []); // eslint-disable-line

  const loadOptions = async (srv) => {
    try { setOptions(await makeApi(srv || server).options()); } catch {}
  };
  const saveServer = async (url) => { await AsyncStorage.setItem('essa_server', url); setServer(url); setScreen('login'); };
  const onLogin = async (tok, usr) => {
    await AsyncStorage.setItem('essa_token', tok); await AsyncStorage.setItem('essa_user', usr || '');
    setToken(tok); setUser(usr); loadOptions(); setScreen('list');
  };
  const logout = async () => { await AsyncStorage.removeItem('essa_token'); setToken(''); setScreen('login'); };

  if (!ready) return <View style={[s.center, { backgroundColor: C.bg }]}><ActivityIndicator color={C.accent} /></View>;

  let body;
  if (screen === 'server') body = <ServerScreen initial={server} onSave={saveServer} />;
  else if (screen === 'login') body = <LoginScreen api={api} onLogin={onLogin} onChangeServer={() => setScreen('server')} />;
  else if (screen === 'detail') body = (
    <DetailScreen api={api} product={selected} options={options} employee={user}
      onBack={() => setScreen('list')} onSaved={() => setScreen('list')} />
  );
  else body = <ListScreen api={api} onPick={(p) => { setSelected(p); setScreen('detail'); }} onLogout={logout} />;

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar style="light" />
      <View style={{ height: Platform.OS === 'ios' ? 44 : 28 }} />
      {body}
    </View>
  );
}

// ---------------- styles ----------------
const s = StyleSheet.create({
  center: { flex: 1, backgroundColor: C.bg, alignItems: 'center', justifyContent: 'center', padding: 24 },
  brand: { color: C.text, fontSize: 24, fontWeight: '800', marginTop: 6 },
  logoAI: { color: C.accent, fontSize: 44, fontWeight: '900', letterSpacing: 2 },
  sub: { color: C.muted, fontSize: 13, marginTop: 4, marginBottom: 18 },
  card: { width: '100%', maxWidth: 420, backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 14, padding: 20 },
  fieldLabel: { color: C.muted, fontSize: 12, marginBottom: 5 },
  input: { backgroundColor: C.panel2, borderColor: C.line, borderWidth: 1, borderRadius: 9, paddingHorizontal: 12, paddingVertical: 11, color: C.text, fontSize: 15 },
  hint: { color: C.muted, fontSize: 11, marginTop: 2, marginBottom: 8 },
  err: { color: '#f2685f', fontSize: 13, marginBottom: 8, textAlign: 'center' },
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
});
