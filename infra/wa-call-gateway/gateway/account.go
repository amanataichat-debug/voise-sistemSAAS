package main

// Аккаунт = один номер WhatsApp, подключённый к шлюзу как связанное устройство (до 4 на номер;
// переписка этого номера остаётся в Evolution, шлюз работает только со звонками).
// Сессия хранится только здесь, в SQLite на сервере; бэкенд знает аккаунт по account_id.

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/mdp/qrterminal/v3"
	whatsmeow "github.com/polymorfa/hypermeow"
	"github.com/polymorfa/hypermeow/store"
	"github.com/polymorfa/hypermeow/types"
	"github.com/polymorfa/hypermeow/types/events"
	"github.com/purpshell/meowcaller"
)

// Состояния аккаунта (account.state → agent_wa_call_accounts.status на бэкенде).
const (
	StateQR           = "qr"           // ждём сканирования QR
	StateQRExpired    = "qr_expired"   // QR не отсканировали вовремя
	StateConnecting   = "connecting"   // сессия есть, соединяемся
	StateConnected    = "connected"    // на связи, звонки принимаются
	StateDisconnected = "disconnected" // связь пропала, переподключаемся сами
	StateLoggedOut    = "logged_out"   // устройство удалено с телефона — нужен новый QR
	StateBanned       = "banned"       // временная блокировка номера WhatsApp
	StateError        = "error"
)

// Account — один номер.
type Account struct {
	ID string // account_id бэкенда

	g     *Gateway
	mu    sync.Mutex
	dev   *store.Device
	wa    *whatsmeow.Client
	calls *meowcaller.Client
	state string
	err   string
	qr    context.CancelFunc

	offersMu sync.Mutex
	offers   map[string]offerInfo // id звонка WhatsApp → данные предложения звонка
}

type offerInfo struct {
	creatorAlt types.JID // номер звонящего, если WhatsApp прислал его рядом с LID
	from       types.JID
	platform   string
	at         time.Time
}

// State — текущее состояние.
func (a *Account) State() (string, string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.state, a.err
}

// Phone — номер аккаунта (E.164 без плюса) или пусто до входа.
func (a *Account) Phone() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.dev != nil && a.dev.ID != nil {
		return a.dev.ID.User
	}
	return ""
}

func (a *Account) connected() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.state == StateConnected && a.wa != nil && a.calls != nil
}

func (a *Account) setState(state, errText string, extra map[string]any) {
	a.mu.Lock()
	changed := a.state != state || a.err != errText
	a.state, a.err = state, errText
	a.mu.Unlock()
	if !changed && extra == nil {
		return
	}
	a.g.log.Info("account state", "account_id", a.ID, "state", state, "error", errText)
	a.g.sendAccountState(a, extra)
}

// Describe — для hello / status / health.
func (a *Account) Describe() map[string]any {
	state, errText := a.State()
	out := map[string]any{"account_id": a.ID, "state": state, "phone": a.Phone()}
	if errText != "" {
		out["error"] = errText
	}
	a.mu.Lock()
	if a.dev != nil && a.dev.PushName != "" {
		out["push_name"] = a.dev.PushName
	}
	a.mu.Unlock()
	return out
}

// newClient собирает клиент WhatsApp + звонков на устройстве. Обработчик событий
// регистрируется ДО библиотеки звонков: он запоминает номер звонящего из предложения
// звонка раньше, чем библиотека вызовет OnIncomingCall.
func (a *Account) newClient(dev *store.Device) {
	wa := whatsmeow.NewClient(dev, newWALogger(a.g.log, a.g.cfg.WALogLevel, "wa/"+short(a.ID)))
	// Только звонки: историю переписки не скачиваем и не храним (переписка — в Evolution)
	wa.ManualHistorySyncDownload = true
	wa.DisableHistorySyncStorage = true
	wa.AutomaticMessageRerequestFromPhone = false
	wa.AddEventHandler(a.onEvent)
	calls := meowcaller.NewClient(wa, meowcaller.WithLogger(newCallLogger(a.g.cfg.WALogLevel)))
	calls.OnIncomingCall(func(c *meowcaller.Call) { a.g.onIncomingCall(a, c) })
	a.mu.Lock()
	a.dev, a.wa, a.calls = dev, wa, calls
	a.offers = map[string]offerInfo{}
	a.mu.Unlock()
}

func short(id string) string {
	if len(id) > 8 {
		return id[:8]
	}
	return id
}

// start поднимает сохранённую сессию (после перезапуска шлюза).
func (a *Account) start(dev *store.Device) {
	a.newClient(dev)
	a.setState(StateConnecting, "", nil)
	if err := a.wa.Connect(); err != nil {
		a.setState(StateError, "connect: "+err.Error(), nil)
	}
}

// login — новый вход по QR. Старая сессия этого аккаунта, если была, заменяется.
func (a *Account) login() {
	a.mu.Lock()
	if a.qr != nil {
		a.qr()
	}
	old := a.wa
	a.mu.Unlock()
	if old != nil {
		old.Disconnect()
	}
	dev := a.g.container.NewDevice()
	a.newClient(dev)
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	a.mu.Lock()
	a.qr = cancel
	wa := a.wa
	a.mu.Unlock()

	ch, err := wa.GetQRChannel(ctx)
	if err != nil {
		cancel()
		a.setState(StateError, "qr: "+err.Error(), nil)
		return
	}
	if err := wa.Connect(); err != nil {
		cancel()
		a.setState(StateError, "connect: "+err.Error(), nil)
		return
	}
	go func() {
		defer cancel()
		for item := range ch {
			switch item.Event {
			case whatsmeow.QRChannelEventCode:
				a.g.log.Info("scan this QR in WhatsApp → Linked devices", "account_id", a.ID,
					"expires_in", item.Timeout.String())
				qrterminal.GenerateHalfBlock(item.Code, qrterminal.L, os.Stdout)
				a.setState(StateQR, "", map[string]any{"qr": item.Code, "expires_in": int(item.Timeout.Seconds())})
			case whatsmeow.QRChannelSuccess.Event:
				a.g.log.Info("QR scanned, device linked", "account_id", a.ID)
			case whatsmeow.QRChannelTimeout.Event:
				a.setState(StateQRExpired, "QR не отсканирован вовремя", nil)
				wa.Disconnect()
			default:
				if item.Error != nil {
					a.setState(StateError, "qr: "+item.Error.Error(), nil)
				} else if item.Event != "" {
					a.setState(StateError, "qr: "+item.Event, nil)
				}
			}
		}
	}()
}

// logout — отвязать устройство от номера и забыть сессию.
func (a *Account) logout() {
	a.mu.Lock()
	wa := a.wa
	if a.qr != nil {
		a.qr()
	}
	a.mu.Unlock()
	if wa != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		if err := wa.Logout(ctx); err != nil {
			a.g.log.Warn("logout error", "account_id", a.ID, "error", err)
			wa.Disconnect()
			a.mu.Lock()
			dev := a.dev
			a.mu.Unlock()
			if dev != nil && dev.ID != nil {
				_ = dev.Delete(ctx)
			}
		}
		cancel()
	}
	a.g.forgetAccount(a.ID)
	a.setState(StateLoggedOut, "", nil)
}

func (a *Account) onEvent(evt any) {
	switch ev := evt.(type) {
	case *events.CallOffer:
		a.offersMu.Lock()
		a.offers[ev.CallID] = offerInfo{creatorAlt: ev.CallCreatorAlt, from: ev.From, platform: ev.RemotePlatform, at: time.Now()}
		for id, o := range a.offers {
			if time.Since(o.at) > 5*time.Minute {
				delete(a.offers, id)
			}
		}
		a.offersMu.Unlock()
	case *events.PairSuccess:
		a.g.rememberAccount(a.ID, ev.ID)
		a.g.log.Info("paired", "account_id", a.ID, "platform", ev.Platform)
	case *events.Connected:
		a.mu.Lock()
		if a.qr != nil {
			a.qr()
			a.qr = nil
		}
		a.mu.Unlock()
		a.setState(StateConnected, "", map[string]any{"connected_at": float64(time.Now().Unix())})
		if a.g.cfg.SendPresence {
			go func() {
				a.mu.Lock()
				wa := a.wa
				a.mu.Unlock()
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				if wa != nil {
					if err := wa.SendPresence(ctx, types.PresenceAvailable); err != nil {
						a.g.log.Warn("send presence failed", "account_id", a.ID, "error", err)
					}
				}
			}()
		}
	case *events.Disconnected:
		if st, _ := a.State(); st == StateConnected {
			a.setState(StateDisconnected, "", nil)
		}
	case *events.LoggedOut:
		a.g.forgetAccount(a.ID)
		a.setState(StateLoggedOut, fmt.Sprintf("устройство отвязано (%v)", ev.Reason), nil)
	case *events.TemporaryBan:
		a.setState(StateBanned, ev.String(), nil)
	case *events.StreamReplaced:
		a.setState(StateError, "сессию открыл другой экземпляр шлюза", nil)
	case *events.ConnectFailure:
		a.setState(StateError, fmt.Sprintf("connect failure %v %s", ev.Reason, ev.Message), nil)
	case *events.ClientOutdated:
		a.setState(StateError, "WhatsApp считает версию клиента устаревшей — нужно обновить шлюз", nil)
	}
}

// callerOf — номер, JID и имя звонящего. WhatsApp всё чаще присылает только LID
// (скрытый идентификатор): тогда номер ищем в таблице LID → номер сессии.
func (a *Account) callerOf(c *meowcaller.Call) (phone, jid, name string) {
	peer := c.Peer()
	jid = peer.String()
	a.offersMu.Lock()
	info, ok := a.offers[c.ID()]
	a.offersMu.Unlock()
	pn := types.EmptyJID
	if ok && info.creatorAlt.Server == types.DefaultUserServer {
		pn = info.creatorAlt
	}
	if pn.IsEmpty() && peer.Server == types.DefaultUserServer {
		pn = peer
	}
	a.mu.Lock()
	wa := a.wa
	a.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if pn.IsEmpty() && wa != nil && peer.Server == types.HiddenUserServer {
		if got, err := wa.Store.LIDs.GetPNForLID(ctx, peer.ToNonAD()); err == nil && !got.IsEmpty() {
			pn = got
		}
	}
	if !pn.IsEmpty() {
		phone = pn.User
	}
	if wa != nil {
		for _, who := range []types.JID{pn, peer.ToNonAD()} {
			if who.IsEmpty() {
				continue
			}
			if ci, err := wa.Store.Contacts.GetContact(ctx, who); err == nil && ci.Found {
				for _, n := range []string{ci.FullName, ci.PushName, ci.BusinessName, ci.FirstName} {
					if n != "" {
						name = n
						break
					}
				}
			}
			if name != "" {
				break
			}
		}
	}
	return phone, jid, name
}

// ---------------------------------------------------------------- список аккаунтов на диске

type accountsFile struct {
	Accounts map[string]string `json:"accounts"` // account_id → JID устройства
}

func (g *Gateway) accountsPath() string { return filepath.Join(g.cfg.DataDir, "accounts.json") }

func (g *Gateway) loadAccountsFile() map[string]string {
	data, err := os.ReadFile(g.accountsPath())
	if err != nil {
		return map[string]string{}
	}
	var f accountsFile
	if json.Unmarshal(data, &f) != nil || f.Accounts == nil {
		return map[string]string{}
	}
	return f.Accounts
}

func (g *Gateway) saveAccountsFile(m map[string]string) {
	data, _ := json.MarshalIndent(accountsFile{Accounts: m}, "", "  ")
	tmp := g.accountsPath() + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err == nil {
		_ = os.Rename(tmp, g.accountsPath())
	}
}

func (g *Gateway) rememberAccount(id string, jid types.JID) {
	g.filesMu.Lock()
	defer g.filesMu.Unlock()
	m := g.loadAccountsFile()
	m[id] = jid.String()
	g.saveAccountsFile(m)
}

func (g *Gateway) forgetAccount(id string) {
	g.filesMu.Lock()
	defer g.filesMu.Unlock()
	m := g.loadAccountsFile()
	if _, ok := m[id]; ok {
		delete(m, id)
		g.saveAccountsFile(m)
	}
}
