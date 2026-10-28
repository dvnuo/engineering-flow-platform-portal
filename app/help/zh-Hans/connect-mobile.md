---
icon: clipboard-check
---
## 用途

在你的电脑上用 BrowserStack 真机录制移动测试步骤。Portal 和助手自己从不连接 BrowserStack：录制通过你电脑上的本地桥接程序进行，测试运行则通过团队的 Jenkins 流水线进行。完整流程请参阅[移动场景测试](#/help/mobile-scenario-testing)。

只有录制移动测试时才需要。

## 设置

- **用户名**和**访问密钥**来自你的 BrowserStack 账号设置。每次录制时，「移动测试」面板会把它们交给你电脑上的本地桥接程序；桥接程序只把它们保存在内存中。
- **测试 BrowserStack** 会通过本地桥接程序从你的电脑登录，显示正在使用的并行会话数，并告诉你这台电脑上是否有 BrowserStack Local；没有的话会给出下载链接。请先启动桥接程序。
- **高级**中是大多数团队不会改动的录制设备设置：默认平台、网络（公开网络，或通过 BrowserStack Local 接入的私有网络）、空闲超时（BrowserStack 最多允许 300 秒）、设备运行的 Appium 版本（不设置时为 2.19.0；BrowserStack 自己的默认版本 1.22.0 不认识 Inspector 的返回、主屏幕和应用切换按钮）、会话视频，以及公司通过自己的网关访问 BrowserStack 时使用的 hub 和 API 地址。测试运行使用流水线自己的设置。

## 本地桥接程序

**连接器 > 本地桥接程序**的程序 `efp-bridge` 负责录制移动测试：它的程序包中带有 `mobile-auto`。它会启动设备，在你录制期间占用设备，并把 Appium Inspector 的命令转发给设备，同时记录你做过的操作。在那个页面安装一次即可；BrowserStack 页面会显示它是否在运行。你的电脑需要能访问 BrowserStack。如果要经过代理，桥接程序会使用你电脑自己的代理设置（静态代理、PAC 脚本或自动检测，和浏览器的做法一样）；你也可以在 BrowserStack 连接器页面上，或在「移动测试」面板的*这台电脑的网络*下设置代理。需要登录的代理，在地址下方的*代理用户名*和*代理密码*字段中填写登录信息（域用户写成 `DOMAIN\user`）。密码字段会隐藏你输入的内容，所以共享屏幕时不会显示出来。登录信息只保存在这台电脑的这个浏览器中，并且只会发给本地桥接程序，再由它交给本机的 mobile-auto 和 BrowserStack Local。在这些字段出现之前保存在地址中的登录信息，会移到这些字段中。如果**测试 BrowserStack** 得到的是公司网页过滤器的拦截页面，请在浏览器中打开同一个地址检查：出现 BrowserStack 的登录提示，说明桥接程序只需要使用浏览器的代理；出现同样的拦截页面，说明网络必须放行 `api-cloud.browserstack.com` 和 `hub-cloud.browserstack.com`。

### BrowserStack Local

私有网络中的应用需要你的电脑上有 BrowserStack 的隧道程序 BrowserStack Local。mobile-auto 首次使用时会自行下载它；如果你的网络阻止了下载，请自己从 BrowserStack 获取（[Windows](https://www.browserstack.com/browserstack-local/BrowserStackLocal-win32.zip)、[macOS](https://www.browserstack.com/browserstack-local/BrowserStackLocal-darwin-x64.zip)，后者适用于 Intel 和 Apple 芯片），解压后把 `BrowserStackLocal.exe`（Windows）或 `BrowserStackLocal`（macOS）放到 `~/bin`（本地桥接程序的安装文件夹），或 PATH 中的任意位置。连接器页面上也有同样的链接。

## 安装包

「移动测试」面板会列出你 BrowserStack 账号中的安装包，并通过桥接程序上传新的安装包。给同一个应用的每个安装包使用相同的自定义 ID，例如 `fxapp-android-uat`，这样录制和流水线总会选用最新的那个。BrowserStack 会在 30 天后删除上传的文件。

## 测试账号的密码

录制永远不会保存你在密码字段中输入的内容：它用一个名称指代每个密码，例如 `MOBILE_SECRET_PASSWORD`，助手会告诉你需要哪些名称。这些值作为移动场景流水线的凭证保存在 Jenkins 中，而不是 Portal 中；请让负责维护 Jenkins 的人添加。

## 私有网络

连接器跟随系统默认时，**网络**来自管理员的「默认连接器」（BrowserStack > 高级）；一旦你自定义了连接器，就由你自己选择，留空表示使用部署的默认值（第一个选项会注明是哪个）。除非应用要访问 BrowserStack 设备无法到达的内部测试服务器，否则选*公开*即可。如果要访问这类服务器，设备就需要 BrowserStack Local：如果你录制所用的电脑能访问这些服务器，选择*私有：每个会话都启动 BrowserStack Local*（这台电脑上必须安装 BrowserStackLocal 程序；桥接程序会把你的代理交给它）；如果已经有长期运行的隧道，选择*你的团队运行的隧道*。具体适用哪一种，请询问平台团队。私有网络会让每次录制都先启动隧道，所以如果一个不需要隧道的应用在录制时因「BrowserStack Local readiness」失败，说明「网络」应改回「公开」。
