(function () {
    menuMobile = function () {
        $('#toggle').on('click.menuMobile', function () {
            $('#backdrop, #menu').show().addClass('on');
            $('#toggle').addClass('active');
            $('html, body').css({'overflow': 'hidden', 'position': 'fixed', 'top': '0', 'left': '0', 'right': '0'});
        });
        $('#backdrop').on('click', function () {
            $('#backdrop, #menu, #sidebar').removeClass('on');
            $('#toggle').removeClass('active');
            $('html, body').css({'overflow': 'auto', 'position': 'static'});
        });
    };
    menuMobile();
})(jQuery);

var st = function () {
    $('html, body').css({'overflow': 'hidden', 'position': 'fixed', 'top': '0', 'left': '0', 'right': '0'});
}

function acceptCookies() {
    localStorage.setItem("cookieConsent", "accepted");
    document.getElementById("cookie-banner").style.display = "none";
}

function declineCookies() {
    localStorage.setItem("cookieConsent", "declined");
    document.getElementById("cookie-banner").style.display = "none";
}

window.onload = function () {
    if (!localStorage.getItem("cookieConsent")) {
        document.getElementById("cookie-banner").style.display = "block";
    }
};