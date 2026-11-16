<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const book = ref([])
const history = ref([])
const skus = ref([])
const err = ref('')
const msg = ref('')
const verify = ref(null)
const adjust = reactive({ sku_code: '', price: '', reason: '' })

async function refresh() {
  book.value = await api.priceBook()
  history.value = await api.history()
  skus.value = await api.skus()
  if (!adjust.sku_code && skus.value.length) adjust.sku_code = skus.value[0].sku_code
}

async function submitAdjust() {
  msg.value = ''
  err.value = ''
  try {
    const r = await api.adjustPrice({
      sku_code: adjust.sku_code,
      price: Number(adjust.price),
      reason: adjust.reason || null,
    })
    msg.value = `已调价 ${r.sku_code}：${r.old_price} → ${r.new_price}`
    adjust.price = ''
    adjust.reason = ''
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function runVerify() {
  msg.value = ''
  err.value = ''
  try {
    verify.value = await api.verifyPrices()
  } catch (e) {
    err.value = e.message
  }
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>现行价签</h1>
    <p>价签本反映已生效的最新价格；下方为历史生效记录（含期望/实际旧价与漂移标记）。手动调价是改价单之外的另一入口，可直接改写价签。</p>
  </section>
  <p v-if="err" class="msg err">{{ err }}</p>
  <p v-if="msg" class="msg ok">{{ msg }}</p>

  <div class="panel">
    <h2>手动调价（其他入口）</h2>
    <div class="row">
      <div class="field">
        <label>商品</label>
        <select v-model="adjust.sku_code">
          <option v-for="s in skus" :key="s.sku_code" :value="s.sku_code">
            {{ s.sku_code }} · {{ s.title }}（现价 {{ s.current_price }}）
          </option>
        </select>
      </div>
      <div class="field"><label>新价</label><input v-model="adjust.price" type="number" step="0.01" /></div>
      <div class="field" style="flex:1"><label>原因</label><input v-model="adjust.reason" placeholder="例如：门店临时促销" /></div>
      <button class="btn warn" type="button" @click="submitAdjust">直接调价</button>
    </div>
    <p class="muted" style="margin:6px 0 0">直接调价立即写入价签与历史；它会让待应用改价单的建单旧价与现行价拉开，应用时触发漂移检测。</p>
  </div>

  <div class="panel">
    <h2>价签本</h2>
    <table>
      <thead>
        <tr><th>SKU</th><th>品名</th><th>分类</th><th>现行价</th><th>来源单</th><th>生效时刻</th><th>更新</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in book" :key="r.sku_code">
          <td>{{ r.sku_code }}</td>
          <td>{{ r.title }}</td>
          <td>{{ r.category }}</td>
          <td>{{ r.current_price }}</td>
          <td>{{ r.source_ticket_no || '初始/手动' }}</td>
          <td>{{ r.effective_at }}</td>
          <td>{{ r.updated_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h2>价签来源互证</h2>
    <button class="btn ghost" type="button" @click="runVerify">核对价签本 ↔ 历史链头</button>
    <div v-if="verify" style="margin-top:10px">
      <p class="msg" :class="verify.ok ? 'ok' : 'err'">
        {{ verify.ok ? `互证通过：${verify.checked} 个 SKU 的价签与历史链头一致` : `发现 ${verify.mismatches.length} 处不一致` }}
      </p>
      <table v-if="verify.mismatches.length">
        <thead>
          <tr><th>SKU</th><th>价签现行价</th><th>价签来源</th><th>链头价格</th><th>链头来源</th><th>问题</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in verify.mismatches" :key="m.sku_code">
            <td>{{ m.sku_code }}</td>
            <td>{{ m.current_price }}</td>
            <td>{{ m.source_ticket_no || '初始/手动' }}</td>
            <td>{{ m.head_price }}</td>
            <td>{{ m.head_source || '初始/手动' }}</td>
            <td>{{ m.problems.join('；') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>价格历史</h2>
    <table>
      <thead>
        <tr><th>SKU</th><th>价格</th><th>来源单</th><th>期望旧价</th><th>实际旧价</th><th>漂移</th><th>生效时刻</th><th>记录时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="h in history" :key="h.id">
          <td>{{ h.sku_code }}</td>
          <td>{{ h.price }}</td>
          <td>{{ h.source_ticket_no || '初始/手动' }}</td>
          <td>{{ h.expected_old_price ?? '—' }}</td>
          <td>{{ h.actual_old_price ?? '—' }}</td>
          <td><span v-if="h.drifted" class="tag warn">漂移</span><span v-else>—</span></td>
          <td>{{ h.effective_at }}</td>
          <td>{{ h.recorded_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
