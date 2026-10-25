import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowLeft, Boxes, ClipboardList, Plus, RefreshCw, Save, Warehouse } from 'lucide-react';

type Variant = { id: string; label: string; price_rub: number };
type Product = { id: number; title: string; variants: Variant[] };
type FlowerItem = {
  id: number;
  title: string;
  unit: string;
  quantity: number;
  reserved_quantity: number;
  available_quantity: number;
  average_cost_rub: number;
  minimum_quantity: number;
  is_low_stock: boolean;
  stock_value_rub: number;
};
type RecipeLine = { item_id: number; item_title: string; unit: string; quantity: number };
type Recipe = {
  product_id: number;
  product_title: string;
  variant_id: string | null;
  variant_label: string | null;
  lines: RecipeLine[];
};
type Movement = {
  id: number;
  item_title: string;
  unit: string;
  type: string;
  quantity: number;
  unit_cost_rub: number;
  total_cost_rub: number;
  note: string;
  order_id: number | null;
  is_reversed: boolean;
  created_at: string;
};
type Overview = {
  items: FlowerItem[];
  total_value_rub: number;
  recipes: Recipe[];
  missing_recipes: Array<{
    product_id: number;
    product_title: string;
    variant_id: string | null;
    variant_label: string | null;
  }>;
  movements: Movement[];
};
type PeriodReport = {
  movement_count: number;
  receipt_value_rub: number;
  expense_value_rub: number;
  waste_value_rub: number;
  sales_cost_rub: number;
  receipt_quantity: number;
  expense_quantity: number;
  waste_quantity: number;
  sold_quantity: number;
};
type RecipePart = { item_id: number; quantity: number };
type Tab = 'stock' | 'recipes' | 'journal';

const API = import.meta.env.VITE_API_URL || '';
const money = (value: number) => new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  maximumFractionDigits: 2,
}).format(value);
const quantity = (value: number) => new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 3,
}).format(value);
const typeLabels: Record<string, string> = {
  receipt: 'Приход',
  expense: 'Расход',
  waste: 'Брак / списание',
  sale: 'Продажа',
  sale_return: 'Возврат продажи',
  reservation: 'Резерв заказа',
  reservation_release: 'Снятие резерва',
};

export default function StockApp() {
  const [apiKey, setApiKey] = useState('');
  const [authorized, setAuthorized] = useState(false);
  const [data, setData] = useState<Overview | null>(null);
  const [tab, setTab] = useState<Tab>('stock');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [itemTitle, setItemTitle] = useState('');
  const [itemUnit, setItemUnit] = useState('стебель');
  const [itemMinimum, setItemMinimum] = useState('0');
  const [movementItem, setMovementItem] = useState('');
  const [movementType, setMovementType] = useState<'receipt' | 'expense' | 'waste'>('receipt');
  const [movementQuantity, setMovementQuantity] = useState('');
  const [movementCost, setMovementCost] = useState('');
  const [movementNote, setMovementNote] = useState('');
  const [selectedProduct, setSelectedProduct] = useState('');
  const [selectedVariant, setSelectedVariant] = useState('');
  const [recipeParts, setRecipeParts] = useState<RecipePart[]>([]);
  const [reportStart, setReportStart] = useState(() => {
    const start = new Date();
    start.setDate(start.getDate() - 29);
    return start.toISOString().slice(0, 10);
  });
  const [reportEnd, setReportEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const [periodReport, setPeriodReport] = useState<PeriodReport | null>(null);

  useEffect(() => {
    document.title = 'Склад цветка — Цветы рядом';
  }, []);

  const products = useMemo<Product[]>(() => {
    if (!data) return [];
    const mapped = new Map<number, Product>();
    for (const recipe of data.recipes) {
      mapped.set(recipe.product_id, {
        id: recipe.product_id,
        title: recipe.product_title,
        variants: [],
      });
    }
    for (const recipe of data.missing_recipes) {
      mapped.set(recipe.product_id, {
        id: recipe.product_id,
        title: recipe.product_title,
        variants: [],
      });
    }
    return Array.from(mapped.values()).sort((a, b) => a.title.localeCompare(b.title, 'ru'));
  }, [data]);

  const currentProduct = products.find((product) => String(product.id) === selectedProduct);
  const recipesForProduct = data?.recipes.filter((recipe) => recipe.product_id === Number(selectedProduct)) ?? [];
  const currentRecipe = recipesForProduct.find((recipe) => (recipe.variant_id ?? '') === selectedVariant);
  const currentMissing = data?.missing_recipes.find(
    (recipe) => recipe.product_id === Number(selectedProduct)
      && (recipe.variant_id ?? '') === selectedVariant,
  );
  const lowStockCount = data?.items.filter((item) => item.is_low_stock).length ?? 0;

  async function apiRequest(path: string, init: RequestInit = {}) {
    const response = await fetch(`${API}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${apiKey}`,
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || 'Не удалось выполнить запрос.');
    return result;
  }

  async function loadOverview(key = apiKey) {
    setBusy(true);
    setError('');
    try {
      const response = await fetch(`${API}/api/admin/stock/overview`, {
        headers: { Authorization: `Bearer ${key}` },
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Не удалось загрузить склад.');
      setData(result as Overview);
      setAuthorized(true);
      setApiKey(key);
      if (!movementItem && result.items[0]) setMovementItem(String(result.items[0].id));
      if (!selectedProduct && result.missing_recipes[0]) {
        setSelectedProduct(String(result.missing_recipes[0].product_id));
        setSelectedVariant(result.missing_recipes[0].variant_id ?? '');
      } else if (!selectedProduct && result.recipes[0]) {
        setSelectedProduct(String(result.recipes[0].product_id));
        setSelectedVariant(result.recipes[0].variant_id ?? '');
      }
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Ошибка соединения.');
      setAuthorized(false);
    } finally {
      setBusy(false);
    }
  }

  async function loadReport(startDate = reportStart, endDate = reportEnd) {
    setBusy(true);
    setError('');
    try {
      const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
      const result = await apiRequest(`/api/admin/stock/report?${params.toString()}`);
      setPeriodReport(result as PeriodReport);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Не удалось загрузить отчёт.');
    } finally {
      setBusy(false);
    }
  }

  async function exportMovements() {
    setBusy(true);
    setError('');
    try {
      const params = new URLSearchParams({ start_date: reportStart, end_date: reportEnd });
      const response = await fetch(`${API}/api/admin/stock/export.csv?${params.toString()}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || 'Не удалось выгрузить журнал.');
      }
      const file = await response.blob();
      const url = URL.createObjectURL(file);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'flower-stock-movements.csv';
      link.click();
      URL.revokeObjectURL(url);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Не удалось выгрузить журнал.');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!currentProduct) return;
    const saved = data?.recipes.find(
      (recipe) => recipe.product_id === currentProduct.id
        && (recipe.variant_id ?? '') === selectedVariant,
    );
    setRecipeParts(saved?.lines.map((line) => ({
      item_id: line.item_id,
      quantity: line.quantity,
    })) ?? []);
  }, [currentProduct?.id, selectedVariant, data?.recipes]);

  async function addItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await apiRequest('/api/admin/stock/items', {
        method: 'POST',
        body: JSON.stringify({
          title: itemTitle.trim(),
          unit: itemUnit.trim(),
          minimum_quantity: Number(itemMinimum),
        }),
      });
      setItemTitle('');
      setItemMinimum('0');
      await loadOverview();
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Не удалось добавить цветок.');
    } finally {
      setBusy(false);
    }
  }

  async function addMovement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await apiRequest('/api/admin/stock/movements', {
        method: 'POST',
        body: JSON.stringify({
          item_id: Number(movementItem),
          movement_type: movementType,
          quantity: Number(movementQuantity),
          ...(movementType === 'receipt' ? { unit_cost_rub: Number(movementCost) } : {}),
          note: movementNote.trim(),
        }),
      });
      setMovementQuantity('');
      setMovementCost('');
      setMovementNote('');
      await loadOverview();
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Не удалось сохранить операцию.');
    } finally {
      setBusy(false);
    }
  }

  async function saveRecipe(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!currentProduct) return;
    setBusy(true);
    setError('');
    try {
      await apiRequest('/api/admin/stock/recipes', {
        method: 'PUT',
        body: JSON.stringify({
          product_id: currentProduct.id,
          variant_id: selectedVariant || null,
          lines: recipeParts.map((part) => ({
            item_id: part.item_id,
            quantity: Number(part.quantity),
          })),
        }),
      });
      await loadOverview();
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Не удалось сохранить рецепт.');
    } finally {
      setBusy(false);
    }
  }

  function addRecipePart() {
    const firstItem = data?.items[0];
    if (!firstItem) {
      setError('Сначала добавьте позиции цветов и приход на склад.');
      return;
    }
    setRecipeParts((previous) => [...previous, { item_id: firstItem.id, quantity: 1 }]);
  }

  const variantOptions = currentProduct
    ? recipesForProduct
      .filter((recipe) => recipe.variant_id)
      .map((recipe) => ({ id: recipe.variant_id!, label: recipe.variant_label || 'Вариант' }))
      .concat(
        (data?.missing_recipes ?? [])
          .filter((recipe) => recipe.product_id === currentProduct.id && recipe.variant_id)
          .map((recipe) => ({ id: recipe.variant_id!, label: recipe.variant_label || 'Вариант' })),
      )
    : [];

  if (!authorized) {
    return (
      <main className="app stock-app">
        <header className="stock-header">
          <a className="stock-back" href="/">← Магазин</a>
          <span><Warehouse size={18} /> Учёт цветка</span>
        </header>
        <section className="stock-login">
          <Boxes size={34} />
          <h1>Складской учёт</h1>
          <p>Войдите с ключом администратора из локального файла настроек магазина.</p>
          <form onSubmit={(event) => { event.preventDefault(); void loadOverview(apiKey); }}>
            <label>
              Ключ администратора
              <input
                type="password"
                autoComplete="current-password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                required
              />
            </label>
            {error && <p className="stock-error" role="alert">{error}</p>}
            <button className="primary full" disabled={busy || !apiKey}>
              {busy ? 'Проверка…' : 'Войти'}
            </button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <main className="app stock-app">
      <header className="stock-header">
        <a className="stock-back" href="/">← Магазин</a>
        <span><Warehouse size={18} /> Учёт цветка</span>
        <button className="secondary" onClick={() => void loadOverview()} disabled={busy}>
          <RefreshCw size={15} /> Обновить
        </button>
      </header>
      <section className="stock-title">
        <div>
          <small>СКЛАД · СРЕДНЕВЗВЕШЕННАЯ ОЦЕНКА</small>
          <h1>Остатки и движение</h1>
        </div>
        <div className="stock-total">
          <small>Стоимость цветка на остатке</small>
          <b>{money(data?.total_value_rub ?? 0)}</b>
        </div>
      </section>
      <nav className="stock-tabs" aria-label="Разделы складского учёта">
        <button className={tab === 'stock' ? 'selected' : ''} onClick={() => setTab('stock')}>
          <Boxes size={16} /> Склад
        </button>
        <button className={tab === 'recipes' ? 'selected' : ''} onClick={() => setTab('recipes')}>
          <ClipboardList size={16} /> Рецепты продаж
        </button>
        <button className={tab === 'journal' ? 'selected' : ''} onClick={() => setTab('journal')}>
          <ClipboardList size={16} /> Журнал
        </button>
      </nav>
      {error && <p className="stock-error" role="alert">{error}</p>}

      {tab === 'stock' && (
        <>
          {lowStockCount > 0 && (
            <p className="stock-warning" role="status">
              Мало товара в {lowStockCount} позициях — проверьте доступные остатки и оформите приход.
            </p>
          )}
          <section className="stock-panel">
            <div className="stock-panel-heading">
              <div><small>НОМЕНКЛАТУРА</small><h2>Цветы на складе</h2></div>
            </div>
            {!data?.items.length ? (
              <p className="stock-empty">Склад пока пуст. Добавьте цветы и оформите первый приход.</p>
            ) : (
              <div className="stock-table-wrap">
                <table className="stock-table">
                  <thead><tr><th>Наименование</th><th>Физический остаток</th><th>В резерве</th><th>Доступно</th><th>Мин. остаток</th><th>Средняя цена</th><th>Стоимость остатка</th></tr></thead>
                  <tbody>{data.items.map((item) => (
                      <tr className={item.is_low_stock ? 'low-stock' : ''} key={item.id}>
                      <td>{item.title}</td>
                      <td>{quantity(item.quantity)} {item.unit}</td>
                        <td>{quantity(item.reserved_quantity)} {item.unit}</td>
                        <td>{quantity(item.available_quantity)} {item.unit}</td>
                        <td>{quantity(item.minimum_quantity)} {item.unit}{item.is_low_stock && item.minimum_quantity > 0 && <strong className="low-stock-label"> · Мало</strong>}</td>
                        <td>{money(item.average_cost_rub)} / {item.unit}</td>
                      <td><b>{money(item.stock_value_rub)}</b></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>
          <div className="stock-forms">
            <section className="stock-panel">
              <small>НОВАЯ ПОЗИЦИЯ</small><h2>Добавить цветок</h2>
              <form onSubmit={addItem} className="stock-form">
                <label>Название<input value={itemTitle} onChange={(event) => setItemTitle(event.target.value)} placeholder="Например, роза Эквадор красная" required minLength={2} maxLength={120} /></label>
                <label>Единица учёта<input value={itemUnit} onChange={(event) => setItemUnit(event.target.value)} placeholder="стебель, пачка, шт." required maxLength={20} /></label>
                <label>Порог предупреждения<input type="number" min="0" step="0.001" value={itemMinimum} onChange={(event) => setItemMinimum(event.target.value)} required /><small>Покажем предупреждение, когда доступного остатка будет не больше этого значения.</small></label>
                <button className="primary" disabled={busy}><Plus size={16} /> Добавить на склад</button>
              </form>
            </section>
            <section className="stock-panel">
              <small>ДВИЖЕНИЕ ТОВАРА</small><h2>Оформить операцию</h2>
              {!data?.items.length ? <p className="stock-empty">Сначала добавьте позицию цветка.</p> : (
                <form onSubmit={addMovement} className="stock-form">
                  <label>Цветок<select value={movementItem} onChange={(event) => setMovementItem(event.target.value)} required>
                    {data.items.map((item) => <option key={item.id} value={item.id}>{item.title} · {quantity(item.quantity)} {item.unit}</option>)}
                  </select></label>
                  <label>Операция<select value={movementType} onChange={(event) => setMovementType(event.target.value as typeof movementType)}>
                    <option value="receipt">Приход</option><option value="expense">Расход</option><option value="waste">Брак / списание</option>
                  </select></label>
                  <label>Количество<input type="number" min="0.001" step="0.001" value={movementQuantity} onChange={(event) => setMovementQuantity(event.target.value)} required /></label>
                  {movementType === 'receipt' && <label>Закупочная цена за единицу, ₽<input type="number" min="0" step="0.01" value={movementCost} onChange={(event) => setMovementCost(event.target.value)} required /></label>}
                  <label>Комментарий<input value={movementNote} onChange={(event) => setMovementNote(event.target.value)} maxLength={500} placeholder="Поставщик, причина списания…" /></label>
                  <button className="primary" disabled={busy}>Записать операцию</button>
                </form>
              )}
            </section>
          </div>
        </>
      )}

      {tab === 'recipes' && (
        <section className="stock-panel">
          <small>АВТОМАТИЧЕСКОЕ СПИСАНИЕ ПРИ ПРОДАЖЕ</small><h2>Состав букета</h2>
          <p className="stock-help">Укажите, сколько каждого цветка уходит на один букет. При оформлении заказа система проверит остаток и спишет компоненты; при отмене заказа вернёт их на склад.</p>
          {!!data?.missing_recipes.length && (
            <p className="stock-warning">Не настроены рецепты для {data.missing_recipes.length} позиций/вариантов. Такие продажи пока не списывают цветы автоматически.</p>
          )}
          <form onSubmit={saveRecipe} className="stock-form recipe-form">
            <label>Букет<select value={selectedProduct} onChange={(event) => {
              const productId = event.target.value;
              setSelectedProduct(productId);
              const missing = data?.missing_recipes.find((recipe) => recipe.product_id === Number(productId));
              const saved = data?.recipes.find((recipe) => recipe.product_id === Number(productId));
              setSelectedVariant(missing?.variant_id ?? saved?.variant_id ?? '');
            }} required>
              {products.map((product) => <option key={product.id} value={product.id}>{product.title}</option>)}
            </select></label>
            {variantOptions.length > 0 && (
              <label>Вариант<select value={selectedVariant} onChange={(event) => setSelectedVariant(event.target.value)}>
                {variantOptions.map((variant) => <option key={variant.id} value={variant.id}>{variant.label}</option>)}
              </select></label>
            )}
            {currentRecipe && currentRecipe.lines.length > 0 && (
              <p className="stock-success">Настроен рецепт: {currentRecipe.lines.length} ингредиента(ов).</p>
            )}
            {!currentRecipe && currentMissing && <p className="stock-warning">Для этого букета рецепт ещё не задан.</p>}
            <div className="recipe-parts">
              {recipeParts.map((part, index) => (
                <div className="recipe-part" key={`${part.item_id}-${index}`}>
                  <select aria-label="Цветок в рецепте" value={part.item_id} onChange={(event) => setRecipeParts((previous) => previous.map((value, i) => i === index ? { ...value, item_id: Number(event.target.value) } : value))}>
                    {data?.items.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.unit}</option>)}
                  </select>
                  <input aria-label="Количество цветка в букете" type="number" min="0.001" step="0.001" value={part.quantity} onChange={(event) => setRecipeParts((previous) => previous.map((value, i) => i === index ? { ...value, quantity: Number(event.target.value) } : value))} />
                  <button className="secondary" type="button" onClick={() => setRecipeParts((previous) => previous.filter((_, i) => i !== index))}>Убрать</button>
                </div>
              ))}
            </div>
            <div className="recipe-actions">
              <button className="secondary" type="button" onClick={addRecipePart}><Plus size={15} /> Добавить цветок</button>
              <button className="primary" disabled={busy || !currentProduct || !data?.items.length}><Save size={15} /> Сохранить рецепт</button>
            </div>
            {!recipeParts.length && <p className="stock-help">Пустой рецепт можно сохранить для товара, который не списывает цветы (например, ваза или шар).</p>}
          </form>
        </section>
      )}

      {tab === 'journal' && (
        <>
          <section className="stock-panel">
            <small>ОТЧЁТ ПО ДВИЖЕНИЮ И СЕБЕСТОИМОСТИ</small><h2>За период</h2>
            <div className="report-filters">
              <label>С<input type="date" value={reportStart} max={reportEnd} onChange={(event) => setReportStart(event.target.value)} /></label>
              <label>По<input type="date" value={reportEnd} min={reportStart} onChange={(event) => setReportEnd(event.target.value)} /></label>
              <button type="button" className="primary" disabled={busy} onClick={() => void loadReport()}><RefreshCw size={15} /> Сформировать</button>
              <button type="button" className="secondary" disabled={busy} onClick={() => void exportMovements()}><ClipboardList size={15} /> Скачать CSV</button>
            </div>
            {periodReport && (
              <div className="report-cards">
                <div><small>Приход</small><b>{quantity(periodReport.receipt_quantity)} · {money(periodReport.receipt_value_rub)}</b></div>
                <div><small>Расход</small><b>{quantity(periodReport.expense_quantity)} · {money(periodReport.expense_value_rub)}</b></div>
                <div><small>Брак / списание</small><b>{quantity(periodReport.waste_quantity)} · {money(periodReport.waste_value_rub)}</b></div>
                <div><small>Себестоимость продаж</small><b>{quantity(periodReport.sold_quantity)} · {money(periodReport.sales_cost_rub)}</b></div>
                <small>{periodReport.movement_count} операций за период</small>
              </div>
            )}
          </section>
          <section className="stock-panel">
            <small>ПОСЛЕДНИЕ 100 ОПЕРАЦИЙ</small><h2>Журнал движения</h2>
            {!data?.movements.length ? <p className="stock-empty">Операций пока нет.</p> : (
              <div className="stock-table-wrap">
                <table className="stock-table">
                  <thead><tr><th>Дата</th><th>Операция</th><th>Цветок</th><th>Количество</th><th>Сумма</th><th>Комментарий</th></tr></thead>
                  <tbody>{data.movements.map((movement) => (
                    <tr className={movement.is_reversed ? 'reversed' : ''} key={movement.id}>
                      <td>{new Date(movement.created_at).toLocaleString('ru-RU')}</td>
                      <td>{typeLabels[movement.type] || movement.type}{movement.is_reversed ? ' · отменено' : ''}</td>
                      <td>{movement.item_title}</td>
                      <td>{quantity(movement.quantity)} {movement.unit}</td>
                      <td>{money(movement.total_cost_rub)}</td>
                      <td>{movement.note || (movement.order_id ? `Заказ №${movement.order_id}` : '—')}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
      <footer className="stock-footer">Стоимость остатка рассчитывается по средневзвешенной закупочной цене.</footer>
      {busy && <div className="stock-busy" role="status">Сохраняем изменения…</div>}
    </main>
  );
}
