// The public API, exactly as the BFF shapes it (services/bff/internal/contract/routes.go).

export type Uuid = string;

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  trace_id?: string;
  errors?: Record<string, string>;
  available?: number;
  requested?: number;
  product_id?: Uuid;
  current_version?: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface CategoryRef { slug: string; name: string }
export interface CategoryNode extends CategoryRef { children: CategoryNode[] }

export interface Image { url: string; position: number }

export interface Product {
  id: Uuid;
  sku: string;
  name: string;
  description: string;
  price_minor: number;
  currency: string;
  category: CategoryRef | null;
  in_stock: boolean;
  images: Image[];
}

export type ProductStatus = "draft" | "active" | "archived";

export interface AdminProduct extends Omit<Product, "images"> {
  images: (Image & { key: string })[];
  status: ProductStatus;
  version: number;
  available_quantity: number;
  created_at: string;
  updated_at: string;
}

export interface CartLine {
  product_id: Uuid;
  sku: string;
  name: string;
  quantity: number;
  unit_price_minor: number;
  currency: string;
  available: boolean;
}

export interface Cart { items: CartLine[]; subtotal_minor: number }

export type OrderStatus = "pending_payment" | "paid" | "payment_failed" | "expired" | "cancelled" | "fulfilled" | "refunded";
export type PaymentStatus = "pending" | "captured" | "failed" | "refunded" | null;

export interface OrderLine { product_id: Uuid; sku: string; name: string; quantity: number; unit_price_minor: number }

export interface Order {
  id: Uuid;
  status: OrderStatus;
  total_minor: number;
  currency: string;
  created_at: string;
  items: OrderLine[];
  payment: { status: PaymentStatus; failure_code: string | null };
}

export interface AdminOrder extends Order { customer_id: Uuid }

export interface Adjustment {
  id: Uuid;
  product_id: Uuid;
  delta: number;
  resulting_quantity: number;
  reason: string;
  created_at: string;
}

export interface Dashboard {
  recent_orders: Order[];
  low_stock: { product_id: Uuid; sku: string; name: string; available_quantity: number }[];
  system: { outbox_pending: number; outbox_oldest_pending_at: string | null };
}

export interface Recommendations {
  items: { product_id: Uuid; sku: string; name: string; price_minor: number; currency: string }[];
  strategy: string;
  degraded?: boolean;
}

export type Role = "customer" | "shop_owner";

export interface Me {
  authenticated: boolean;
  csrf_token: string;
  login_url?: string;
  user?: { id: Uuid; email: string; roles: Role[] };
}

export type Sort = "relevance" | "newest" | "price_asc" | "price_desc" | "name";

export interface ProductQuery {
  q?: string;
  category?: string;
  min_price?: number;
  max_price?: number;
  sort?: Sort;
  page?: number;
  page_size?: number;
}

export interface ProductInput {
  name: string;
  description: string;
  price_minor: number;
  currency: string;
  category: string;
  category_name?: string;
  status: "draft" | "active";
  sku: string;
  initial_stock?: number;
}

export type PaymentSource = "tok_visa" | "tok_mastercard" | "tok_declined" | "tok_insufficient" | "tok_timeout";

export interface Line { product_id: Uuid; quantity: number }

/** Every call the app makes. Two implementations: the BFF (http.ts) and an in-browser demo (demo.ts). */
export interface Api {
  readonly demo: boolean;
  /** Demo only: start the shop afresh. */
  reset?(): void;
  me(): Promise<Me>;
  signIn(returnTo: string, as?: Role): Promise<void>;
  signOut(): Promise<void>;
  register(email: string, password: string): Promise<void>;

  products(q: ProductQuery): Promise<Page<Product>>;
  product(id: Uuid): Promise<Product>;
  categories(): Promise<CategoryNode[]>;
  recommendations(productId?: Uuid, limit?: number): Promise<Recommendations>;

  cart(): Promise<Cart>;
  setCart(items: Line[]): Promise<Cart>;
  checkout(items: Line[], idempotencyKey: string): Promise<Order>;
  orders(page?: number): Promise<Page<Order>>;
  order(id: Uuid): Promise<Order>;
  pay(id: Uuid, source: PaymentSource): Promise<Order>;

  admin: {
    dashboard(): Promise<Dashboard>;
    products(q: { q?: string; status?: ProductStatus; category?: string; page?: number }): Promise<Page<AdminProduct>>;
    product(id: Uuid): Promise<AdminProduct>;
    create(p: ProductInput): Promise<AdminProduct>;
    update(id: Uuid, version: number, p: Partial<Omit<ProductInput, "sku" | "initial_stock">>): Promise<AdminProduct>;
    archive(id: Uuid): Promise<AdminProduct>;
    restore(id: Uuid): Promise<AdminProduct>;
    adjust(id: Uuid, delta: number, reason: string): Promise<Adjustment>;
    adjustments(id: Uuid): Promise<Page<Adjustment>>;
    orders(q: { status?: OrderStatus; page?: number }): Promise<Page<AdminOrder>>;
    refund(id: Uuid): Promise<AdminOrder>;
  };
}

/** A failed call, carrying the RFC 9457 problem the BFF sent. */
export class ApiError extends Error {
  constructor(readonly problem: Problem) {
    super(problem.detail || problem.title);
  }
  get status() { return this.problem.status; }
  get kind() { return this.problem.type.split("/").pop() ?? ""; }
}
