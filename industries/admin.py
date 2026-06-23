from django.contrib import admin

from . import models


admin.site.register(models.SectorModule)
admin.site.register(models.WorkProject)
admin.site.register(models.ProjectCostEntry)
admin.site.register(models.ProductRecipe)
admin.site.register(models.ProductRecipeLine)
