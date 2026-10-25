import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { Minus, Plus, Search, ShoppingBag, Sparkles, X } from 'lucide-react';
import StockApp from './StockApp';

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData: string;
        ready: () => void;
        expand: () => void;
      };
    };
  }
}

type Product = {
  id: number;
  title: string;
  description: string;
  category: string;
  categories?: string[];
  price_rub: number;
  variants?: ProductVariant[];
  image_url: string;
  stock: number;
};

type ProductVariant = { id: string; label: string; price_rub: number };

type ShopInfo = {
  delivery_fee_rub: number;
  pickup_address: string;
};

type Line = { product: Product; variant?: ProductVariant; quantity: number };
type FulfillmentMethod = 'delivery' | 'pickup';

const API = import.meta.env.VITE_API_URL || '';
const DEFAULT_SHOP_INFO: ShopInfo = {
  delivery_fee_rub: 350,
  pickup_address: 'Красноярск, проспект Мира, 5',
};
const rub = (amount: number) =>
  new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: 'RUB',
    maximumFractionDigits: 0,
  }).format(amount);

export default function App() {
  const [products, setProducts] = useState<Product[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState(false);
  const [shopInfo, setShopInfo] = useState(DEFAULT_SHOP_INFO);
  const [cart, setCart] = useState<Record<string, Line>>({});
  const [selectedVariants, setSelectedVariants] = useState<Record<number, string>>({});
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [checkout, setCheckout] = useState(false);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiAnswer, setAiAnswer] = useState('');
  const [fulfillmentMethod, setFulfillmentMethod] =
    useState<FulfillmentMethod>('delivery');
  const [ai, setAi] = useState({
    occasion: 'День рождения',
    budget_rub: 3000,
    preferences: '',
  });
  const [form, setForm] = useState({
    customer_name: '',
    phone: '',
    address: '',
    delivery_at: '',
    note: '',
  });
  const init = window.Telegram?.WebApp?.initData || '';

  useEffect(() => {
    window.Telegram?.WebApp?.ready();
    window.Telegram?.WebApp?.expand();
    fetch(`${API}/api/products`)
      .then((response) => {
        if (!response.ok) throw new Error('Не удалось загрузить каталог.');
        return response.json();
      })
      .then((items: Product[]) => setProducts(items))
      .catch(() => setCatalogError(true))
      .finally(() => setCatalogLoading(false));
    fetch(`${API}/api/shop-info`)
      .then((response) => {
        if (!response.ok) throw new Error('Не удалось загрузить условия доставки.');
        return response.json();
      })
      .then(setShopInfo)
      .catch(() => setNotice('Не удалось загрузить условия доставки.'));
  }, []);
  useEffect(() => {
    if (!checkout && !aiOpen) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setCheckout(false);
        setAiOpen(false);
      }
    }
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [checkout, aiOpen]);

  const productCategories = (product: Product) =>
    (product.categories?.length ? product.categories : [product.category])
      .filter((item) => item !== '1 сентября');
  const categories = [
    ...Array.from(
      new Set(products.flatMap(productCategories)),
    ).filter((item) => item !== '1 сентября'),
  ];
  const shown = products.filter((product) => {
    return (
      (!category || productCategories(product).includes(category)) &&
      `${product.title} ${product.description}`.toLowerCase().includes(search.toLowerCase())
    );
  });
  const lines = Object.values(cart);
  const total = useMemo(
    () => lines.reduce(
      (sum, line) => sum + (line.variant?.price_rub ?? line.product.price_rub) * line.quantity,
      0,
    ),
    [lines],
  );
  const cartQuantity = lines.reduce((sum, line) => sum + line.quantity, 0);
  const deliveryFee = fulfillmentMethod === 'delivery' ? shopInfo.delivery_fee_rub : 0;

  if (window.location.pathname === '/stock') return <StockApp />;

  function cartKey(product: Product, variant?: ProductVariant) {
    return `${product.id}:${variant?.id ?? 'base'}`;
  }

  function selectedVariant(product: Product) {
    return product.variants?.find((variant) => variant.id === selectedVariants[product.id])
      ?? product.variants?.[0];
  }

  function add(product: Product, delta: number, variant?: ProductVariant) {
    if (product.stock <= 0) {
      setNotice('Этой позиции сейчас нет в наличии.');
      return;
    }
    const key = cartKey(product, variant);
    setCart((previous) => {
      const next = { ...previous };
      const productQuantity = Object.values(next)
        .filter((line) => line.product.id === product.id)
        .reduce((sum, line) => sum + line.quantity, 0);
      if (delta > 0 && productQuantity >= Math.min(product.stock, 20)) return previous;
      const quantity = (next[key]?.quantity || 0) + delta;
      if (quantity <= 0) delete next[key];
      else next[key] = { product, variant, quantity };
      return next;
    });
    const productQuantity = lines
      .filter((line) => line.product.id === product.id)
      .reduce((sum, line) => sum + line.quantity, 0);
    if (delta > 0 && productQuantity >= Math.min(product.stock, 20)) {
      setNotice('Достигнуто максимальное количество этого букета в заказе.');
    }
  }

  async function order(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!init) {
      setNotice('Откройте магазин через Telegram, чтобы оформить заказ.');
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/orders`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Telegram-Init-Data': init,
        },
        body: JSON.stringify({
          items: lines.map((line) => ({
            product_id: line.product.id,
            quantity: line.quantity,
            ...(line.variant ? { variant_id: line.variant.id } : {}),
          })),
          ...form,
          address: fulfillmentMethod === 'delivery' ? form.address : '',
          fulfillment_method: fulfillmentMethod,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw Error(data.detail || 'Не удалось оформить заказ.');
      setNotice(
        `Заказ №${data.order_id} отправлен флористу на подтверждение. После подтверждения администратор пришлёт способ оплаты.`,
      );
      setCart({});
      setCheckout(false);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Сетевая ошибка.');
    } finally {
      setBusy(false);
    }
  }

  async function recommend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/ai/recommend`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Telegram-Init-Data': init,
        },
        body: JSON.stringify(ai),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'AI временно недоступен.');
      setAiAnswer(data.recommendation || data.detail || 'Попробуйте позже.');
    } catch (error) {
      setAiAnswer(error instanceof Error ? error.message : 'AI временно недоступен.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app">
      <header>
        <div className="brand">
          <img src="/logo.svg" alt="" width="44" height="44" />
          <div>
            <b>Цветы рядом</b>
            <small>Цветочная мастерская · Красноярск</small>
          </div>
        </div>
        <button
          className="bag"
          aria-label={cartQuantity ? `Корзина, ${cartQuantity} товаров` : 'Корзина пуста'}
          onClick={() => {
            if (cartQuantity) setCheckout(true);
            else setNotice('Добавьте букет в корзину, чтобы оформить заказ.');
          }}
        >
          <ShoppingBag /> {cartQuantity}
        </button>
      </header>

      <section className="hero">
        <div>
          <small>СОБРАНО С ЗАБОТОЙ В КРАСНОЯРСКЕ</small>
          <h1>Пусть чувства<br />расцветают</h1>
          <p>Авторские букеты для тех самых моментов. Подберём, бережно соберём и доставим.</p>
          <button
            className="primary"
            onClick={() => document.getElementById('catalog')?.scrollIntoView({ behavior: 'smooth' })}
          >
            Выбрать букет
          </button>
        </div>
        <img className="hero-mark" src="/logo.svg" alt="" />
      </section>
      <div className="features">
        <span><b>01</b> Бережно собираем вручную</span>
        <span><b>02</b> Доставляем по Красноярску</span>
        <span><b>03</b> Удобное время — при заказе</span>
      </div>

      <section id="catalog">
        <div className="section">
          <div>
            <small>НАША КОЛЛЕКЦИЯ</small>
            <h2>Букеты и цветы</h2>
          </div>
          <button className="ai" onClick={() => setAiOpen(true)}>
            <Sparkles size={16} /> AI-подбор
          </button>
        </div>
        <label className="search">
          <Search size={18} />
          <input
            aria-label="Поиск по каталогу"
            placeholder="Найти букет..."
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <nav aria-label="Категории каталога">
          {categories.map((item) => (
            <button
              className={category === item ? 'selected' : ''}
              key={item}
              onClick={() => setCategory(item)}
            >
              {item}
            </button>
          ))}
        </nav>
        {catalogLoading && (
          <p className="empty" role="status">Загружаем свежие букеты…</p>
        )}
        {catalogError && (
          <div className="empty empty-error" role="alert">
            <span>Не удалось загрузить каталог. Проверьте соединение и попробуйте ещё раз.</span>
            <button className="secondary" onClick={() => window.location.reload()}>Обновить</button>
          </div>
        )}
        {!catalogLoading && !catalogError && shown.length === 0 && (
          <div className="empty">
            <span>{products.length ? 'Ничего не нашлось. Измените поиск или выберите другую категорию.' : 'Каталог скоро появится.'}</span>
            {(search || category) && (
              <button
                className="secondary"
                onClick={() => {
                  setSearch('');
                  setCategory('');
                }}
              >
                Сбросить фильтры
              </button>
            )}
          </div>
        )}
        <div className="grid">
          {shown.map((product) => (
            <article className="product" key={product.id}>
              <img
                src={product.image_url}
                alt={product.title}
                loading="lazy"
                decoding="async"
              />
              <div className="info">
                <small>{productCategories(product).join(' · ') || 'Букет'}</small>
                <h3>{product.title}</h3>
                {product.description && <p>{product.description}</p>}
                {product.variants && product.variants.length > 0 && (
                  <label className="variant-select">
                    Вариант букета
                    <select
                      value={selectedVariant(product)?.id ?? ''}
                      onChange={(event) =>
                        setSelectedVariants({
                          ...selectedVariants,
                          [product.id]: event.target.value,
                        })
                      }
                    >
                      {product.variants.map((variant) => (
                        <option key={variant.id} value={variant.id}>
                          {variant.label} — {rub(variant.price_rub)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <div className="bottom">
                  <b>{rub(selectedVariant(product)?.price_rub ?? product.price_rub)}</b>
                  {cart[cartKey(product, selectedVariant(product))] ? (
                    <div className="qty">
                      <button onClick={() => add(product, -1, selectedVariant(product))}><Minus size={14} /></button>
                      {cart[cartKey(product, selectedVariant(product))].quantity}
                      <button onClick={() => add(product, 1, selectedVariant(product))}><Plus size={14} /></button>
                    </div>
                  ) : product.stock > 0 ? (
                    <button className="add" onClick={() => add(product, 1, selectedVariant(product))}>
                      <Plus size={14} /> Добавить
                    </button>
                  ) : (
                    <button className="add" disabled title="Нет в наличии">
                      Нет в наличии
                    </button>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>

      <footer>🌷 Цветы рядом · Красноярск <small>Каталог магазина</small></footer>

      {lines.length > 0 && !checkout && (
        <div className="cartbar">
          <div>
            <small>{lines.length} позиций</small>
            <b>{rub(total + deliveryFee)}</b>
          </div>
          <button className="primary" onClick={() => setCheckout(true)}>Оформить заказ</button>
        </div>
      )}

      {(checkout || aiOpen) && (
        <div
          className="overlay"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setCheckout(false);
              setAiOpen(false);
            }
          }}
        >
          <section
            className="sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="dialog-title"
          >
            <div className="sheethead">
              <h2 id="dialog-title">{checkout ? 'Оформление заказа' : 'Подбор букета с AI'}</h2>
              <button
                className="close"
                onClick={() => {
                  setCheckout(false);
                  setAiOpen(false);
                }}
              >
                <X />
              </button>
            </div>
            {checkout ? (
              <>
                <div className="fulfillment">
                  <label>
                    <input
                      type="radio"
                      name="fulfillment"
                      checked={fulfillmentMethod === 'delivery'}
                      onChange={() => setFulfillmentMethod('delivery')}
                    />
                    Доставка · {rub(shopInfo.delivery_fee_rub)}
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="fulfillment"
                      checked={fulfillmentMethod === 'pickup'}
                      onChange={() => setFulfillmentMethod('pickup')}
                    />
                    Самовывоз · бесплатно
                  </label>
                  {fulfillmentMethod === 'pickup' && (
                    <p>Адрес самовывоза: {shopInfo.pickup_address}</p>
                  )}
                </div>
                <div className="lines">
                  {lines.map((line) => (
                    <div key={cartKey(line.product, line.variant)}>
                      <span>
                        {line.product.title} × {line.quantity}
                        {line.variant && <small>{line.variant.label}</small>}
                      </span>
                      <b>{rub((line.variant?.price_rub ?? line.product.price_rub) * line.quantity)}</b>
                    </div>
                  ))}
                  <div>
                    {fulfillmentMethod === 'pickup' ? 'Самовывоз' : 'Доставка'}
                    <b>{deliveryFee === 0 ? 'Бесплатно' : rub(deliveryFee)}</b>
                  </div>
                  <strong>Итого<b>{rub(total + deliveryFee)}</b></strong>
                </div>
                <form onSubmit={order}>
                  {([
                    ['customer_name', 'Имя'],
                    ['phone', 'Телефон'],
                    ...(fulfillmentMethod === 'delivery'
                      ? ([['address', 'Адрес доставки']] as const)
                      : []),
                    ['delivery_at', 'Дата/время доставки или самовывоза'],
                    ['note', 'Текст открытки или комментарий'],
                  ] as const).map(([key, label]) => (
                    <label key={key}>
                      {label}
                      <input
                        type={key === 'phone' ? 'tel' : 'text'}
                        autoComplete={
                          key === 'customer_name'
                            ? 'name'
                            : key === 'phone'
                              ? 'tel'
                              : key === 'address'
                                ? 'street-address'
                                : undefined
                        }
                        maxLength={
                          key === 'note'
                            ? 1000
                            : key === 'address'
                              ? 500
                              : key === 'delivery_at'
                                ? 100
                                : 160
                        }
                        required={['customer_name', 'phone', 'address'].includes(key)}
                        value={form[key]}
                        onChange={(event) =>
                          setForm({ ...form, [key]: event.target.value })
                        }
                      />
                    </label>
                  ))}
                  <p className="payment-hint">
                    Флорист подтвердит заказ и пришлёт инструкции по оплате.
                  </p>
                  <button className="primary full" disabled={busy || !lines.length}>
                    {busy ? 'Отправка…' : 'Отправить заказ'}
                  </button>
                </form>
              </>
            ) : (
              <>
                <form onSubmit={recommend}>
                  <label>
                    Повод
                    <input
                      required
                      maxLength={200}
                      value={ai.occasion}
                      onChange={(event) => setAi({ ...ai, occasion: event.target.value })}
                    />
                  </label>
                  <label>
                    Бюджет: {rub(ai.budget_rub)}
                    <input
                      type="range"
                      min="500"
                      max="15000"
                      step="500"
                      value={ai.budget_rub}
                      onChange={(event) =>
                        setAi({ ...ai, budget_rub: +event.target.value })
                      }
                    />
                  </label>
                  <label>
                    Пожелания
                    <input
                      maxLength={500}
                      value={ai.preferences}
                      onChange={(event) =>
                        setAi({ ...ai, preferences: event.target.value })
                      }
                    />
                  </label>
                  <button className="primary full" disabled={busy}>
                    Подобрать букет
                  </button>
                </form>
                {aiAnswer && <p className="answer">{aiAnswer}</p>}
              </>
            )}
          </section>
        </div>
      )}
      {notice && (
        <div className="notice" role="status" aria-live="polite">
          <span>{notice}</span>
          <button aria-label="Закрыть уведомление" onClick={() => setNotice('')}>
            <X size={16} />
          </button>
        </div>
      )}
    </main>
  );
}
