# PostPDF Mobil — Instagram fotoğraflarından PDF ve Android yazdırma

Telefon ekranına uygun **PWA** (ana ekrana eklenebilen web uygulaması). Kullanım: Instagram gönderi linkini yapıştır → PDF oluştur → **PDF'i indir** veya **Android'de yazdır**.

## Neler var?

- Instagram'daki **fotoğraflı `/p/` gönderileri**: kaydırmalı fotoğraf gönderilerinin fotoğraflarını sırasıyla tek PDF'e dönüştürme.
- **Android yazdırma:** Yazdır düğmesi aynı fotoğrafları A4 yazdırma görünümünde açar, telefonun normal sistem yazdırma penceresine yönlendirir. Mobil tarayıcı otomatik açmayı engellerse yazdırma sayfasındaki **Yazdır** düğmesine bir kez dokun. **Yazıcı ve kağıt seçimi kullanıcı onayı gerektirir**; sessiz doğrudan yazdırma web uygulamasıyla mümkün değildir.
- **PDF indir** düğmesi; Orijinal oran veya A4 düzeni.
- Instagram erişimi kısıtlarsa **Fotoğraf yükle**: telefondan fotoğraf seçerek aynı PDF/yazdırma akışı.
- PWA manifesti, ikonlar ve servis çalışanı; Android Chrome/Samsung Internet'te ana ekrana eklenebilir. **Uygulama internetsiz PDF dönüştürmez**; sunucu gerekir.
- İsteğe bağlı sunucu tarafı Instagram oturumu. **Kullanıcı adı/şifre HTML/JavaScript/ZIP içine konulmaz.**
- `APP_PIN` ile erişim kodu; PDF ve fotoğraflar farklı cihaz/oturuma gösterilmez. Bellekteki sonuçlar 45 dakika sonra silinir veya sunucu yeniden başladığında kaybolur.

## 1) Telefonumda hemen nasıl açarım?

Bu ZIP **yüklemeye hazır uygulama kaynak kodudur**, internette açılmış bir adres veya Android `.apk` değildir. Telefonun herhangi bir yerden erişebilmesi için bir sunucuya **HTTPS** ile yayımlamak gerekir. Basit seçenek: GitHub'a yükleyip Render'da Docker web servisi oluşturmak.

### Render ile yayımlama

1. Bu klasörün içeriğini özel (private) bir GitHub deposuna aktar. **Hiçbir şifre veya oturum çerezi yükleme.**
2. Render'da **New → Blueprint** oluştur ve GitHub deposunu seç (`render.yaml` hazır). Alternatif olarak **Web Service → Docker** ile ekle.
3. Render ortam değişkenleri:
   - `APP_PIN`: Başkalarının kullanmasını engellemek için **güçlü, yeni bir kişisel erişim kodu** seç.
   - `APP_SECRET_KEY`: `render.yaml` otomatik üretir; manuel kurulumda uzun rastgele değer ekle.
   - `COOKIE_SECURE=1` (Render HTTPS arkasında çalıştırırken).
   - **İsteğe bağlı oturum** için `IG_USERNAME` ve `IG_PASSWORD` değerlerini **sadece Render → Environment** ekranına **secret** olarak ekle. Instagram bu yöntemle girişe izin vermeyebilir, doğrulama isteyebilir veya erişimi kısıtlayabilir. Aşağıdaki çerez yöntemi daha güvenilir olabilir. **Bu değişkenleri kaynak dosyalara yazma.**
4. Render'ın verdiği `https://...onrender.com` adresini Android Chrome'da aç.
5. Chrome menüsünden **Uygulamayı yükle / Ana ekrana ekle** seçeneğine dokun.
6. Instagram linkiyle dene; PDF oluşunca **Android'de yazdır** → yazıcını seç → Yazdır. Telefonun yazdırma hizmetinin veya üretici yazdırma eklentisinin kurulu olması gerekebilir.

> Ücretsiz barındırma planları uykuya geçebilir veya sınırlı kalabilir. Erişim kapasitesi ve fiyatları seçtiğin servis üzerinden doğrula. PWA kurulumunun çalışması için **HTTPS** gerekir (localhost istisnadır).

### Oturum çerezi (genellikle daha stabil)

`gallery-dl` kendi hesabınızdan giriş yapılmış bir tarayıcının **Netscape biçimindeki cookies.txt dosyasını** kullanabilir. Bu dosya hesaba erişim sağlayabilen gizli bir anahtardır; **kimseyle paylaşma, GitHub'a yükleme, bu sohbete ekleme.** Dosyayı yalnızca *kendi* sunucunda özel bir yola koyup `IG_COOKIES_FILE=/gizli/dizin/cookies.txt` ortam değişkenini tanımla. Bu seçeneğin `IG_USERNAME`/`IG_PASSWORD` değerlerine göre önceliği vardır. Render gibi platformlarda kalıcı, gizli dosya sağlamak ayrı bir işlem gerektirir; sadece ortam değişkeni vermek dosyayı yaratmaz.

**Instagram platform kurallarına dikkat:** Oturum açmak herkese açık veya özel her gönderiye erişim hakkı sağlamaz, otomatik erişimi garanti etmez ve Instagram'ın otomatik toplama kurallarını değiştirmez. Kendi hesabına ait veya açık izin aldığın fotoğraflarla kullan. İzin dışı toplu indirme yapma.

## 2) Bilgisayarda çalıştırıp Android tarayıcıda kullanma

Python 3.10+ yükle. Terminalde:

```bash
python -m pip install -r requirements.txt
python -m uvicorn mobile_app:app --host 0.0.0.0 --port 8000
```

Bilgisayar ve telefon **aynı Wi-Fi'de** olsun. Telefonda `http://BILGISAYARIN_YEREL_IP:8000` aç. Bu yöntemle web uygulaması kullanılabilir; ancak **HTTPS olmadığı için Android'e PWA olarak kurulum desteklenmeyebilir**. Yerel ağda kullanıyorsan `APP_PIN` belirle. Çevrimiçi kullanım için internete açık, HTTPS'li sunucu gerekir.

**Windows PowerShell** ortam değişkeni örneği (kendi değerlerini gir):

```powershell
$env:APP_PIN = "yeni_ve_uzun_bir_erisim_kodu"
$env:APP_SECRET_KEY = "yeni_uzun_rastgele_uygulama_anahtari"
python -m uvicorn mobile_app:app --host 0.0.0.0 --port 8000
```

`IG_USERNAME` / `IG_PASSWORD` kullanımı için yalnızca sunucuya gizli değişkenler girilmeli; proje içine sabitlenmemeli.

## Sorun giderme

- **Fotoğraflar alınamıyor:** Instagram oturum doğrulaması, hız sınırı, silinmiş/gizli gönderi veya değişen Instagram arayüzü. Sunucu oturumunu kontrol et veya **Fotoğraf yükle** seçeneğini kullan. **Her gönderi için başarı garantisi yok.**
- **Reels/video:** bu sürüm yalnızca fotoğraf slaytlarını toplar.
- **Yazdırma penceresi açılmıyor:** açılan yazdırma sayfasındaki **Yazdır** düğmesine dokun. Android ayarlarından yazdırma hizmetini ve yazıcı bağlantısını kontrol et.
- **Ana ekrana ekleme görünmüyor:** HTTPS adresiyle Chrome/Samsung Internet kullan; tarayıcı desteği sürüme göre değişebilir.
- **PDF bulunamadı:** PDF sonuçları 45 dakika sonra veya sunucu yeniden başladığında silinir; yeniden oluştur.
- **Yükleme sınırı:** 30 fotoğraf, tek görsel en fazla 20 MB, toplam 60 MB.

## Proje dosyaları

| Dosya | İşlev |
|---|---|
| `mobile_app.py` | FastAPI sunucusu, mobil API, oturum/erişim kontrolü, PDF/print servisleri |
| `converter.py` | İndirilen fotoğraflardan PDF üretimi ve opsiyonel sunucu Instagram oturumu |
| `templates/` | Mobil arayüz, giriş ve Android yazdırma sayfası |
| `static/` | Tasarım, JS, PWA manifesti/ikonları |
| `Dockerfile` + `render.yaml` | Render/Docker dağıtımı |
| `tests/` | Çevrimdışı birim ve HTTP uygulama testleri |

Testler:

```bash
python -m pip install pytest httpx pymupdf
python -m pytest -q
```

**Güvenlik notu:** Daha önce bir yerde açık metin olarak paylaştığın Instagram şifreni değiştirmeni, iki faktörlü doğrulamayı açmanı öneririz. Bu proje içinde hiçbir gerçek Instagram şifresi veya çerezi bulunmaz.
