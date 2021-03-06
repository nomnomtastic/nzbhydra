angular
    .module('nzbhydraApp')
    .controller('UpdateFooterController', UpdateFooterController);

function UpdateFooterController($scope, $http, UpdateService) {

    $scope.updateAvailable = false;
    
    $http.get("internalapi/mayseeadminarea").then(function(data) {
       if (data.data.mayseeadminarea) {
           UpdateService.getVersions().then(function (data) {
               $scope.currentVersion = data.data.currentVersion;
               $scope.repVersion = data.data.repVersion;
               $scope.updateAvailable = data.data.updateAvailable;
               if ($scope.repVersion > $scope.currentVersion) {
                   UpdateService.getChangelog().then(function (data) {
                       $scope.changelog = data.data.changelog;
                   })
               }
           });
       } 
    });
    
    
    

    $scope.update = function () {
        UpdateService.update();
    };

    $scope.showChangelog = function () {
        UpdateService.showChanges($scope.changelog);
    }

}
