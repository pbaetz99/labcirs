from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.views.static import serve

from cirs.admin import admin_site
from cirs.views import DepartmentList, login_user, logout_user

urlpatterns = [
    re_path(r'^$', DepartmentList.as_view(), name='labcirs_home'),
    re_path(r'^incidents/', include('cirs.urls')),
    re_path(r'^admin/logout/$', logout_user, name='logout_admin'),
    re_path(r'^admin/', admin_site.urls),
    re_path(r'^login/$',  login_user, name='login'),
    re_path(r'^logout/$', logout_user, name='logout'),
    re_path(r'^i18n/', include('django.conf.urls.i18n')),
    # Only the password reset views, not django.contrib.auth.urls, which would add a second login.
    path('accounts/password_reset/', auth_views.PasswordResetView.as_view(),
         name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(),
         name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(),
         name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(),
         name='password_reset_complete'),
    #re_path(r'^docs/', include('docs.urls')),
]

# Error pages without context processors, see cirs.views.server_error
handler400 = 'cirs.views.bad_request'
handler500 = 'cirs.views.server_error'

if settings.DEBUG:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {
            'document_root': settings.MEDIA_ROOT,
        }),
    ]
