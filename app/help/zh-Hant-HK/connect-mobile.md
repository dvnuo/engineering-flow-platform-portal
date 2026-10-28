---
icon: clipboard-check
---
## 用途

在你的電腦上用 BrowserStack 真機錄製流動測試步驟。Portal 和助手本身從不連接 BrowserStack：錄製經由你電腦上的本機橋接程式進行，測試執行則經由團隊的 Jenkins 流水線進行。完整流程請參閱[流動情境測試](#/help/mobile-scenario-testing)。

只有錄製流動測試時才需要。

## 設定

- **用戶名稱**和**存取密鑰**來自你的 BrowserStack 帳戶設定。每次錄製時，「流動測試」面板會把它們交給你電腦上的本機橋接程式；橋接程式只把它們保存在記憶體中。
- **測試 BrowserStack** 會經由本機橋接程式從你的電腦登入，顯示正在使用的並行工作階段數目，並告訴你這部電腦上有沒有 BrowserStack Local；沒有的話會提供下載連結。請先啟動橋接程式。
- **進階**中是大多數團隊不會改動的錄製裝置設定：預設平台、網絡（公開網絡，或經由 BrowserStack Local 連接的私有網絡）、閒置逾時（BrowserStack 最多允許 300 秒）、裝置執行的 Appium 版本（不設定時為 2.19.0；BrowserStack 自己的預設版本 1.22.0 不認識 Inspector 的返回、主畫面和應用程式切換按鈕）、工作階段影片，以及公司經由自己的網關存取 BrowserStack 時使用的 hub 和 API 地址。測試執行使用流水線自己的設定。

## 本機橋接程式

**連接器 > 本機橋接程式**的程式 `efp-bridge` 負責錄製流動測試：它的套件附帶 `mobile-auto`。它會啟動裝置，在你錄製期間佔用裝置，並把 Appium Inspector 的指令轉發給裝置，同時記錄你做過的操作。在那個頁面安裝一次即可；BrowserStack 頁面會顯示它是否正在執行。你的電腦需要能連到 BrowserStack。如果要經過代理，橋接程式會使用你電腦本身的代理設定（靜態代理、PAC 腳本或自動偵測，與瀏覽器的做法一樣）；你也可以在 BrowserStack 連接器頁面上，或在「流動測試」面板的*這部電腦的網絡*下設定代理。需要登入的代理，請在地址下方的*代理用戶名稱*和*代理密碼*欄位中填寫登入資料（網域用戶寫成 `DOMAIN\user`）。密碼欄位會隱藏你輸入的內容，所以分享螢幕時不會顯示出來。登入資料只保存在這部電腦的這個瀏覽器中，而且只會傳給本機橋接程式，再由它交給本機的 mobile-auto 和 BrowserStack Local。在這些欄位出現之前儲存在地址中的登入資料，會移到這些欄位中。如果**測試 BrowserStack** 得到的是公司網頁過濾器的封鎖頁面，請在瀏覽器中開啟同一個地址檢查：出現 BrowserStack 的登入提示，表示橋接程式只需要使用瀏覽器的代理；出現同樣的封鎖頁面，表示網絡必須放行 `api-cloud.browserstack.com` 和 `hub-cloud.browserstack.com`。

### BrowserStack Local

私有網絡中的應用程式需要你的電腦上有 BrowserStack 的隧道程式 BrowserStack Local。mobile-auto 首次使用時會自行下載它；如果你的網絡阻止了下載，請自己從 BrowserStack 取得（[Windows](https://www.browserstack.com/browserstack-local/BrowserStackLocal-win32.zip)、[macOS](https://www.browserstack.com/browserstack-local/BrowserStackLocal-darwin-x64.zip)，後者適用於 Intel 和 Apple 晶片），解壓後把 `BrowserStackLocal.exe`（Windows）或 `BrowserStackLocal`（macOS）放到 `~/bin`（本機橋接程式的安裝資料夾），或 PATH 中的任何位置。連接器頁面上也有相同的連結。

## 安裝檔

「流動測試」面板會列出你 BrowserStack 帳戶中的安裝檔，並經由橋接程式上載新的安裝檔。為同一個應用程式的每個安裝檔使用相同的自訂 ID，例如 `fxapp-android-uat`，這樣錄製和流水線總會選用最新的那個。BrowserStack 會在 30 天後刪除上載的檔案。

## 測試帳戶的密碼

錄製永遠不會儲存你在密碼欄位中輸入的內容：它用一個名稱指代每個密碼，例如 `MOBILE_SECRET_PASSWORD`，助手會告訴你需要哪些名稱。這些值作為流動情境流水線的憑證保存在 Jenkins 中，而不是 Portal 中；請負責管理 Jenkins 的人新增。

## 私有網絡

連接器跟隨系統預設時，**網絡**來自管理員的「預設連接器」（BrowserStack > 進階）；一旦你自訂了連接器，就由你自己選擇，留空表示使用部署的預設值（第一個選項會註明是哪一個）。除非應用程式要存取 BrowserStack 裝置無法到達的內部測試伺服器，否則選*公開*即可。如果要存取這類伺服器，裝置就需要 BrowserStack Local：如果你錄製所用的電腦能存取這些伺服器，選擇*私有：每個工作階段都啟動 BrowserStack Local*（這部電腦上必須安裝 BrowserStackLocal 程式；橋接程式會把你的代理交給它）；如果已經有長期運行的隧道，選擇*你的團隊運行的隧道*。具體適用哪一種，請向平台團隊查詢。私有網絡會令每次錄製都先啟動隧道，所以如果一個不需要隧道的應用程式在錄製時因「BrowserStack Local readiness」失敗，表示「網絡」應改回「公開」。
